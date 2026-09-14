# Setup Guide

## Prerequisites

- Python 3.11
- PostgreSQL 14+ (asyncpg driver)
- Redis 6+
- MinIO or any S3-compatible store (optional; required for async exports)
- For CUDA inference: an ONNX Runtime build with the CUDA execution provider

## 1. Environment

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set at minimum:

| variable | purpose |
| --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/attendance` |
| `REDIS_URL` | rate limiting + Celery |
| `JWT_SECRET` | required in production (>= 32 chars) |
| `SMTP_*`, `EMAIL_BASE_URL` | password-reset delivery |

## 2. Model weights

```bash
python scripts/export_pretrained_onnx.py --validate
# -> models/exported/embedding_net.onnx (~94 MB)
# -> downloads VGGFace2 weights once, verifies shape/norms via ONNX Runtime
```

`EMBEDDING_DIM` must equal the ONNX output dimension (512 for FaceNet). The
engine logs a warning and the matcher skips vectors whose stored dimension
differs from the loaded model.

## 3. Database schema

```bash
alembic upgrade head
alembic current          # verify revision
```

The API refuses to start when `alembic_version` does not match the migration
head, preventing silent drift between code and schema.

## 4. Bootstrap tenants

```bash
python -m scripts.bootstrap \
    --org-name "Acme Corp" --org-slug acme \
    --admin-email admin@acme.com \
    [--admin-password '...']   # prompted securely if omitted
```

Idempotent: re-running prints existing records; `--reset-password` rotates the
admin password.

## 5. Run services

Development:

```bash
uvicorn src.api.main:app --reload
celery -A src.workers.celery_app worker -Q default,embedding,export -c 2 -l info
celery -A src.workers.celery_app beat -l info        # optional locally
```

Production:

```bash
cd deployment
# export DB_USER DB_PASSWORD REDIS_PASSWORD JWT_SECRET MINIO_SECRET_KEY ...
docker compose up -d --build
docker compose --profile training up -d trainer       # optional GPU trainer
```

## 6. First login / API key

```bash
curl -sX POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@acme.com","password":"...","organization_slug":"acme"}'
```

Create a kiosk API key (shown exactly once):

```bash
curl -sX POST localhost:8000/api/admin/api-keys \
  -H "Authorization: Bearer $ACCESS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"front-gate","scopes":["recognition:write","attendance:write"]}'
```

## 7. Register faces & verify recognition

1. `POST /api/recognize/users/{id}/register` with 3–5 base64 JPEG/PNG frames
   (org-admin token). Poor-quality frames are rejected with per-category counts.
2. `POST /api/recognize` with a frame + API key or user token. Blink and
   head-movement need several frames, so include `liveness_frames_base64` for a
   full liveness check; a single frame is scored on static texture only.
3. Check `/health` (`indexed_users`) and `/metrics` (`matcher_index_size`).

## 8. Run the test suite

```bash
pip install -r requirements-dev.txt
pytest tests -m "not ml"   # fast: no model, torch or MediaPipe required
pytest tests               # full suite incl. ONNX + MediaPipe paths
```

The ML tests skip automatically when `models/exported/embedding_net.onnx` is
absent, so the fast suite is safe to run without exporting the model first.

## Verification checklist

- [ ] `ruff check src scripts tests` clean, `ruff format --check` clean
- [ ] `pytest tests` green (69 tests)
- [ ] `GET /health` returns `database: ok`
- [ ] `GET /metrics` exposes `http_requests_total`
- [ ] Login → refresh → logout round-trip works; replaying the old refresh
      token returns 401 and revokes sessions
- [ ] Logout invalidates the *access* token too (reuse it → 401)

## Troubleshooting

| symptom | cause / fix |
| --- | --- |
| startup error about schema version | run `alembic upgrade head` |
| `Refusing to start in production: JWT_SECRET...` | export a strong secret |
| startup fails on `CORS_ORIGINS` | values need a scheme, e.g. `https://app.example.com` (comma-separate multiple) |
| every recognition returns `is_live=false`, reason `error` | model file missing (run step 2) |
| every recognition returns `liveness_details` starting `static_only` | expected for single-frame requests; send `liveness_frames_base64` for blink/movement |
| recognition always misses | embeddings stored by a different-dimension model — rebuild indexes via the beat task |
| async export fails `NoSuchBucket` | MinIO buckets not provisioned; check worker startup logs |
| reset email never arrives | SMTP unconfigured: check logs for the ops error on request |
