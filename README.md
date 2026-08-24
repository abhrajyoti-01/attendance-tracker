# Attendance Tracker

Production-grade, multi-tenant face recognition attendance system.

FastAPI · SQLAlchemy 2 (async) · PostgreSQL · Redis · Celery · ONNX Runtime (FaceNet) · Docker

## What this is

A backend that lets kiosks and cameras mark attendance by recognizing registered
faces, with organization-level isolation, liveness anti-spoofing, audit trails,
and operational tooling (migrations, metrics, health probes, background workers).

**Recognition model:** FaceNet InceptionResnetV1 pretrained on VGGFace2,
exported to ONNX (512-dim L2-normalized embeddings). Optional per-organization
fine-tuning via a batch-hard triplet pipeline (`src/training/`).

## Repository layout

```
src/
  api/            FastAPI app: routes, schemas, dependencies, middleware, metrics
  services/       Auth tokens, password reset, email, audit, storage, attendance
                  rules, event bus (Redis pub/sub + local fan-out)
  database/       ORM models (13 tables) and async session/engine
  inference/      Embedding engine (ONNX), matcher math, per-org index registry
  preprocessing/  MTCNN face detection, image quality gates
  anti_spoofing/  MediaPipe liveness (EAR blink / head movement / LBP texture)
  training/       Triplet dataset + PK sampler, single-GPU trainer, evaluator
  workers/        Celery app and tasks: embeddings, training jobs, exports,
                  maintenance
dashboard/        React + TypeScript admin SPA (Vite), nginx-served in Docker
scripts/          bootstrap.py (first org/admin), export_pretrained_onnx.py
migrations/       Alembic environment + baseline migration
deployment/       docker-compose.yml, hardened Dockerfiles, nginx, Prometheus
```

## Quick start (development)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt                 # test/lint tooling

# 1. Provision PostgreSQL and Redis (or use deployment/docker-compose.yml)
# 2. Configure environment
cp .env.example .env                                # fill in DATABASE_URL etc.

# 3. Export the pretrained embedding model (~94 MB ONNX)
python scripts/export_pretrained_onnx.py --validate

# 4. Apply schema migrations
alembic upgrade head

# 5. Create your first organization + superadmin (idempotent)
python -m scripts.bootstrap \
    --org-name "Acme Corp" --org-slug acme \
    --admin-email admin@acme.com

# 6. Run the API
uvicorn src.api.main:app --reload
```

Login against `/api/auth/login` with `{email, password, organization_slug}`.
Swagger UI is available at `/docs` outside production.

## Web dashboard

A React + TypeScript admin SPA lives in `dashboard/`. It covers sign-in with
token rotation, the summary/charts overview, a **real-time attendance feed**
(SSE over `/api/dashboard/stream`), user management, departments, and
organization settings.

```bash
# Development (Vite dev server proxies /api to localhost:8000)
cd dashboard && npm install && npm run dev

# Production build
cd dashboard && npm run build   # strict tsc + Vite bundle in dist/
```

In Docker, enable it with its compose profile:

```bash
docker compose --profile dashboard up -d --build
# SPA on http://localhost:${DASHBOARD_PORT:-3000}; nginx proxies /api to the api service
```

## Production deployment

```bash
cd deployment
export DB_USER=... DB_PASSWORD=... REDIS_PASSWORD=... JWT_SECRET=...
export MINIO_SECRET_KEY=... CORS_ORIGINS=https://attendance.example.com
docker compose up -d --build
```

The stack starts Postgres, Redis, MinIO, the migration job, the API (4 workers),
a Celery worker pool, Celery beat, and nginx. TLS certificates drop into
`deployment/nginx/certs/{fullchain.pem,privkey.pem}` and the commented blocks in
`nginx.conf` are marked `[tls-only]`.

Production guardrails (the process refuses to start otherwise):
- explicit `JWT_SECRET` ≥ 32 chars
- `DEBUG=false`, no wildcard CORS origins

## Security model

- **JWT access tokens** (30 min default) + **refresh tokens with rotation**:
  every refresh invalidates the presented token; replaying a rotated token
  revokes all of that user's sessions (theft detection). Logout revokes server-side.
- **Password reset**: one-time hashed tokens stored in `password_reset_tokens`,
  delivered by SMTP email only — never returned in HTTP responses.
- **API keys**: HMAC-hashed at rest, scoped (`recognition:write`,
  `attendance:write`), shown once at creation, revocable from the admin API.
- **Tenant isolation**: recognition indexes and all queries are scoped per
  organization; cross-org reads return 404.
- **Audit log** for logins, credential changes, user/org/API-key operations,
  settings changes, and exports.
- **Rate limiting** twice over: nginx zones plus an in-app Redis-backed limiter
  (auth 10/min/IP, recognition 60/min/IP, general 100/min/IP).
- **bcrypt** password hashing with the 72-byte input limit enforced explicitly.

## Recognition flow

```
POST /api/recognize  (Bearer user or X-API-Key)
  base64 image → MTCNN detect+crop → quality gate (optional)
  → liveness check (blink/movement/texture) → spoof attempt logged if failed
  → ONNX embedding (512-d, L2-normalized)
  → cosine match against the caller's organization index only
  → optional auto-marking of attendance with dedupe window
```

Indexes live in memory per organization (`MatcherRegistry`), warm up at startup,
update instantly on registration/deactivation, and rebuild periodically via beat.

## Model fine-tuning (optional)

```bash
POST /api/admin/training-jobs   # superadmin; config.data_root required
{
  "config": {"data_root": "/app/data/faces/acme", "epochs": 10}
}
```

Data layout: one directory per identity containing face photos. The trainer
splits identity-disjoint holdout sets, mines batch-hard triplets (P identities ×
K samples), evaluates TPR@FAR=1e-3 each epoch, checkpoints the best model, and
exports it to ONNX. Jobs run on the dedicated `training` queue.

## Operations

| Concern | Where |
| --- | --- |
| Health | `GET /health` — db critical, redis/model degrade |
| Metrics | `GET /metrics` (Prometheus format) |
| Migrations | `alembic upgrade head`; startup verifies schema currency |
| Background tasks | Celery queues `default, embedding, export, training` |
| Scheduled cleanup | beat: expired tokens nightly, spoof records after 90d, index refresh every 30 min |
| Live feed | `GET /api/dashboard/stream` — SSE, org-scoped, admin-only (`ENABLE_LIVE_FEED`) |
| Alerts | `deployment/prometheus/alerts.yml` |

## Quality gates

```bash
ruff check src scripts
ruff format --check src scripts
```

CI (`.github/workflows/ci.yml`) runs lint and format checks and builds both
Docker images on push.

## Honest limitations

- **Liveness is heuristic** (EAR blink, nose movement, LBP texture). It raises
  the bar against photo/replay attacks but is not PAD-certified. For hostile
  environments add an IR/depth sensor path.
- **Embeddings are global per org**, not per device; threshold calibration
  should be re-checked when lighting/camera fleets change materially.
- **Fine-tuning needs data discipline**: ≥2 identities × ≥4 photos to start,
  realistically dozens of identities for measurable gains over VGGFace2 base.
