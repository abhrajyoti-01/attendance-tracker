# Attendance Tracker

[![CI](https://github.com/abhrajyoti-01/attendance-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/abhrajyoti-01/attendance-tracker/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/abhrajyoti-01/attendance-tracker)](https://github.com/abhrajyoti-01/attendance-tracker/releases)
[![License](https://img.shields.io/badge/license-not%20set-lightgrey)](#license)
[![Python](https://img.shields.io/badge/python-3.11-blue)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-69%20passing-brightgreen)](tests/)

Production-grade, multi-tenant face recognition attendance system.

FastAPI · SQLAlchemy 2 (async) · PostgreSQL · Redis · Celery · ONNX Runtime (FaceNet) · React + TypeScript · Docker

## What this is

A full-stack system that lets kiosks and cameras mark attendance by recognizing
registered faces, with organization-level isolation, liveness anti-spoofing,
audit trails, and operational tooling (migrations, metrics, health probes,
background workers) — plus an admin dashboard with a real-time event feed.

**Recognition model:** FaceNet InceptionResnetV1 pretrained on VGGFace2,
exported to ONNX (512-dim L2-normalized embeddings). Optional per-organization
fine-tuning via a batch-hard triplet pipeline (`src/training/`).

## Features

- **Face recognition attendance** — MTCNN detection, quality-gated enrollment,
  512-d embeddings, per-organization in-memory cosine/FAISS matching.
- **Liveness anti-spoofing** — LBP texture, blink and head-movement checks that
  fail closed and record spoof attempts.
- **Multi-tenancy** — every query and match index is scoped by organization;
  cross-org access returns 404.
- **Auth** — JWT access tokens + rotating refresh tokens with theft detection;
  revocable access tokens; HMAC-hashed, scoped API keys for kiosks.
- **Admin dashboard** — React + TypeScript SPA: overview charts, live SSE feed,
  user/department management, organization settings, dark mode, WCAG AA.
- **Operations** — Alembic migrations with startup schema verification,
  Prometheus metrics, structured JSON logs with correlation IDs, Celery beat
  maintenance, Docker Compose stack with healthchecks.

## Screenshots

> Add screenshots of the sign-in page, overview dashboard and live feed here.

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

The stack starts Postgres, Redis, MinIO, the migration job (which the API now
waits for), the API (`API_WORKERS`, default 4), a Celery worker pool, Celery
beat, and nginx. nginx serves plain HTTP by default so the stack comes up
without certificates; to enable TLS, drop `fullchain.pem`/`privkey.pem` into
`deployment/nginx/certs/` and start nginx with `NGINX_CONF=nginx-tls.conf`
(see `docs/deployment.md`).

Production guardrails (the process refuses to start otherwise):
- explicit `JWT_SECRET` ≥ 32 chars
- `DEBUG=false`, no wildcard CORS origins
- `CORS_ORIGINS` entries must include a scheme (`https://app.example.com`)

## Security model

- **JWT access tokens** (30 min default) + **refresh tokens with rotation**:
  every refresh invalidates the presented token; replaying a rotated token
  revokes all of that user's sessions (theft detection). Logout, password
  change and admin resets bump `users.token_version`, which invalidates every
  outstanding access token immediately — not just the refresh token.
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
  base64 frame → MTCNN detect → 160x160 RGB uint8 crop
  → liveness (LBP texture now; blink/head-movement from liveness_frames_base64)
  → spoof attempt recorded and request short-circuited if not live
  → ONNX embedding (512-d, L2-normalized)
  → cosine match against the caller's organization index only
  → optional auto-marking of attendance with dedupe window
```

Enrollment is separate: `POST /api/recognize/users/{id}/register` takes 3–5
frames, quality-gates each one (sharpness, brightness, contrast, face size),
averages the surviving embeddings and stores the L2-normalized mean.

Thresholds come from the caller's organization settings (falling back to global
defaults) and are floored at `MATCH_THRESHOLD_FLOOR`.

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
ruff check src scripts tests
ruff format --check src scripts tests
pytest tests -m "not ml"          # fast suite: no model/torch needed
pytest tests                       # full suite incl. ONNX + MediaPipe
```

CI (`.github/workflows/ci.yml`) runs lint, the test suite, a strict
`tsc --noEmit` + Vite build of the dashboard, and builds all three Docker
images on push.

Requires the exported model for the ML tests:
`python scripts/export_pretrained_onnx.py --validate`. Without it those tests
skip rather than fail.

## Recognition request shape

`POST /api/recognize` accepts a primary frame plus an optional short burst used
for the temporal liveness checks:

```json
{
  "image_base64": "<primary frame>",
  "liveness_frames_base64": ["<frame 2>", "<frame 3>"],
  "check_liveness": true,
  "auto_mark_attendance": false
}
```

Blink and head-movement cannot be measured from one frame, so a single-frame
request is scored on static texture alone and reports
`liveness_details: "static_only..."`. Set `SPOOF_REQUIRE_SEQUENCE=true` to
reject single-frame requests outright. Thresholds are read per organization
from the dashboard settings page; the recognition threshold can never be
lowered below `MATCH_THRESHOLD_FLOOR`.

## Honest limitations

- **Liveness is heuristic** (LBP texture, blink, head movement). It raises the
  bar against photo/replay attacks but is not PAD-certified. For hostile
  environments add an IR/depth sensor path.
- **Single-frame liveness is texture-only.** Blink and movement need a frame
  burst; without one, only static texture is judged.
- **Embeddings are global per org**, not per device; threshold calibration
  should be re-checked when lighting/camera fleets change materially.
- **Fine-tuning needs data discipline**: ≥2 identities × ≥4 photos to start,
  realistically dozens of identities for measurable gains over VGGFace2 base.
- **Access tokens are revocable via `users.token_version`.** Logout, password
  change and admin reset bump it, so outstanding tokens fail immediately.
- **Face images are not persisted.** `face_images` and
  `spoof_attempts.snapshot_key` are reserved but never written; only the
  aggregated embedding is stored. MinIO holds export files only.
- **The Docker stack was not exercised end-to-end** in this environment
  (no Docker available); it is verified by linting, the SQLite-backed test
  suite and type-checked builds.

## License

No license has been set for this repository. Until one is added, all rights are
reserved by the author and the code may not be reused or redistributed.

