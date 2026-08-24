# Architecture

## System Overview

The Attendance Tracker is a multi-tenant, production-grade face recognition attendance system designed for organizations with 5,000 to 50,000+ users.

```
                         Nginx Reverse Proxy
                    (TLS termination, rate limiting)
                              │
              ┌───────────────┴───────────────┐
              │                               │
    FastAPI Server(s)                  React Dashboard
    (gunicorn + uvicorn)               (planned)
              │
   ┌──────────┼──────────┐
   │          │          │
PostgreSQL   Redis    MinIO / S3
(primary)  (cache,     (face images)
           broker)
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
              │ HTTP/WebSocket
```

## Key Design Decisions

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Database** | PostgreSQL 15+ | MVCC, concurrent writes, JSONB, partitioning support |
| **Cache/Broker** | Redis 7+ | Embedding cache, rate-limit counters, Celery broker, pub/sub |
| **File Storage** | MinIO (dev) / S3 (prod) | Face images stored separately from DB |
| **Background Tasks** | Celery + Redis | Async embedding computation, training jobs, report generation |
| **ML Inference** | ONNX Runtime | 2-3x faster CPU inference vs PyTorch, cross-platform |
| **Vector Search** | FAISS (optional) | IVF-PQ for 100K+ vectors, sub-millisecond nearest-neighbor |
| **Monitoring** | Prometheus | Request latency, match latency, error rates, queue depth |

## Data Flow

### Face Recognition Pipeline

```
Webcam Frame (640x480)
  → Face Detection (MTCNN, every 3rd frame)
  → Quality Check (blur, lighting, face size)
  → Anti-Spoofing (blink detection, head movement, texture analysis)
  → Align & Crop (160x160)
  → ONNX Inference → 128-d embedding
  → Cosine/FAISS Match vs cached embeddings
  → Score > threshold? → Identify user or mark unknown
```

### Attendance Marking Flow

```
Recognition result
  → Debounce check (last marked < 10 min? skip)
  → POST /api/attendance
  → DB insert (PostgreSQL)
  → SSE broadcast to dashboard
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
└── dashboard/               # React frontend (planned)
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
