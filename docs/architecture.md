# Architecture

## System Overview

The Attendance Tracker is a multi-tenant face recognition attendance system designed for organizations with 5,000 to 50,000+ users.

```
                         Nginx Reverse Proxy
                    (TLS termination, rate limiting)
                              │
              ┌───────────────┴───────────────┐
              │                               │
    FastAPI Server(s)                  React Dashboard
    (uvicorn, N workers)               (nginx-served SPA)
              │
   ┌──────────┼──────────┐
   │          │          │
PostgreSQL   Redis    MinIO / S3
(primary)  (cache,     (exports; face images
           broker)     are not persisted)
              │
   ┌──────────┴──────────┐
   │    Celery Workers    │
   │  - Embedding compute │
   │  - Training jobs     │
   │  - Report exports    │
   └──────────────────────┘

   Recognition Stations (N)
   ┌──────────┐  ┌──────────┐
   │ Laptop 1 │  │ Kiosk 1  │  ...
   │ (webcam) │  │ (IP cam) │
   └────┬─────┘  └────┬─────┘
        └──────────────┘
              │ HTTP (REST + SSE live feed)
```

## Key Design Decisions

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Database** | PostgreSQL 15+ | MVCC, concurrent writes, JSONB, partitioning support |
| **Cache/Broker** | Redis 7+ | Embedding cache, rate-limit counters, Celery broker, pub/sub |
| **File Storage** | MinIO (dev) / S3 (prod) | Attendance export files; face images are not persisted |
| **Background Tasks** | Celery + Redis | Async embedding computation, training jobs, report generation |
| **ML Inference** | ONNX Runtime | 2-3x faster CPU inference vs PyTorch, cross-platform |
| **Vector Search** | FAISS (optional) | IVF-PQ for 100K+ vectors, sub-millisecond nearest-neighbor |
| **Monitoring** | Prometheus | Request latency, match latency, error rates, queue depth |

## Data Flow

### Face Recognition Pipeline

```
Webcam Frame (640x480)
  → Face Detection (MTCNN) → 160x160 RGB uint8 crop
  → Quality Check (sharpness, brightness, contrast, face size)
  → Anti-Spoofing (texture from one frame; blink/head movement from a frame burst)
  → ONNX Inference → 512-d L2-normalized embedding
  → Cosine/FAISS Match vs per-organization cached embeddings
  → Score > threshold? → Identify user or mark unknown
```

``FaceDetector.detect`` returns a **uint8 RGB** crop in [0, 255] and
``EmbeddingEngine`` applies ``(x/255 - 0.5) / 0.5`` exactly once, matching the
exported graph (see `scripts/export_pretrained_onnx.py`). Liveness is temporal:
blink and head-movement require several frames, so a single-frame request is
scored only on static texture. Send `liveness_frames_base64` for the full check.

### Attendance Marking Flow

```
Recognition result
  → Dedupe check (last marked inside the window? skip)
  → POST /api/attendance  (or auto-mark on /api/recognize)
  → DB insert (PostgreSQL)
  → SSE broadcast to dashboard (/api/dashboard/stream)
```

## Directory Structure

```
attendance_tracker/
├── src/
│   ├── api/                 # FastAPI application (routes, schemas, dependencies)
│   │   ├── main.py          # App entry point, lifespan, middleware, routes
│   │   ├── dependencies.py  # Dependency injection (auth, RBAC, pagination)
│   │   ├── routes/          # auth, users, recognition, attendance, dashboard
│   │   └── schemas/         # Pydantic request/response models
│   ├── database/            # SQLAlchemy ORM models & async session
│   ├── inference/           # ONNX Runtime engine + cosine/FAISS matcher
│   ├── anti_spoofing/       # Liveness detection + challenge-response
│   ├── preprocessing/       # MTCNN face detector, quality checker, augmentations
│   ├── model/               # PyTorch model definitions + loss functions
│   ├── workers/             # Celery app + async task definitions
│   └── utils/               # Security (JWT, bcrypt), structured logging
├── deployment/              # Docker Compose, Dockerfiles, Nginx config
├── data/                    # Raw, processed, and pretraining images
├── models/                  # Model checkpoints, exported ONNX files
├── docs/                    # Documentation
└── dashboard/               # React frontend (React 18 + Vite, nginx-served)
```

## Multi-Tenancy

All data is scoped by `organization_id`. Each table includes this foreign key. The API enforces tenant isolation through:
- JWT tokens include `organization_id` and `role`
- Route-level dependency injection verifies tenant membership
- RBAC: `superadmin` (system-wide), `org_admin` (tenant), `member` (tenant user)

## Scalability

- **Stateless API servers**: Horizontally scalable behind Nginx load balancer
- **Async database**: asyncpg + SQLAlchemy async for non-blocking DB operations
- **ONNX Runtime**: Optimized inference on CPU, 2-3x faster than PyTorch eager
- **FAISS integration**: For organizations exceeding 5,000 users, switch from NumPy to FAISS IVF-PQ index
- **Attendance partitioning**: Partition by month for tables with 50K+ daily entries
- **Celery workers**: Separate queues for embedding computation, training, and exports
