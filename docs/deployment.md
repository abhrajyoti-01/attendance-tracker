# Deployment Guide

## Stack

```
nginx :80/443 -> api (uvicorn x4) -> PostgreSQL, Redis, MinIO
                 worker (default/embedding/export queues)
                 beat   (scheduled maintenance)
                 trainer (training queue; docker compose --profile training)
```

All images run as non-root uid 10001 with pinned dependencies and healthchecks.

## One-time preparation

1. **Secrets** — nothing has a default in production:

```bash
export JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export DB_USER=attendance DB_PASSWORD=...
export REDIS_PASSWORD=...
export MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=...
export CORS_ORIGINS=https://attendance.example.com
```

The API process refuses to start with a missing/weak `JWT_SECRET`, `DEBUG=true`,
or wildcard CORS when `ENV=production`.

2. **Model** — bake or mount the ONNX model:

```bash
python scripts/export_pretrained_onnx.py --validate
# models/exported/embedding_net.onnx is baked into both images via the build context;
# alternatively mount a shared volume at /app/models (compose uses models_data volume)
```

3. **TLS** — certificates are opt-in via a separate nginx config, so the stack
   starts on plain HTTP without them:

```bash
# 1. Place certificates where the nginx container already mounts them:
#      deployment/nginx/certs/fullchain.pem
#      deployment/nginx/certs/privkey.pem
# 2. Switch nginx to the TLS server block (port 80 redirects to 443,
#    except /healthz which orchestrators poll directly):
cd deployment
NGINX_CONF=nginx-tls.conf docker compose up -d nginx
```

`deployment/nginx/nginx-tls.conf` is a complete drop-in replacement for
`nginx.conf` that adds `ssl_certificate` directives, TLS 1.2/1.3, OCSP
stapling and HSTS. Without certificates, nginx exits with
"no ssl_certificate is defined", so do not point `NGINX_CONF` at it until the
files exist.

## Bring-up

```bash
cd deployment
docker compose up -d --build
docker compose logs -f migrate      # alembic upgrade head runs first
curl -s localhost/healthz           # {"status":"healthy",...}
```

Service order is enforced by healthchecks: postgres → redis → migrate → api.
Beat and worker join after redis. The trainer waits behind the `training`
profile so GPU resources are only pulled when requested.

## Resource limits

| service | memory cap | rationale |
| --- | --- | --- |
| postgres | 1g | working set |
| redis | 768m | rate-limit counters + celery results |
| api | 4g | `${API_WORKERS:-4}` uvicorn workers × ONNX sessions |
| worker | 4g | detection+embedding concurrency 2 |
| trainer | 8g | batch-hard mining peak |

## Observability

- Prometheus scrape config: `deployment/prometheus/prometheus.yml` targeting
  `api:8000/metrics`; alert rules in `alerts.yml` cover 5xx rate, p95 latency,
  spoof-check spikes, and empty matcher index.
- Logs are JSON on stdout (`LOG_FORMAT=json`); every request carries
  `X-Correlation-ID`. Ship stdout to your aggregator of choice.
- `/metrics` is denied by default at nginx; allow your monitoring subnet there.

## Scheduled maintenance (Celery beat)

| task | schedule | purpose |
| --- | --- | --- |
| cleanup_expired_tokens_task | daily 03:00 UTC | purge consumed/expired auth tokens |
| purge_old_spoof_attempts_task | daily 03:30 UTC | spoof records past 90 days |
| refresh_matcher_indexes_task | every 30 min | rebuild org indexes from DB |

## Zero-downtime notes

- Migrations run as a separate one-shot job before the new API starts; keep them
  backward-compatible within one release (add columns first, drop later).
- The matcher registry rebuilds from the embeddings table, so API replicas stay
  consistent without cross-node state.
- Worker containers use `--max-tasks-per-child=50` to bound memory drift from
  native libraries.

## Production checklist

- [ ] Secrets exported; no defaults anywhere
- [ ] TLS certificates mounted and nginx switched to `nginx-tls.conf`
- [ ] `alembic current` matches head inside the api container
- [ ] Bootstrap executed once: superadmin exists, default password rotated
- [ ] SMTP configured (password reset depends on it)
- [ ] `MINIO_*` set and the worker has logged bucket provisioning (async exports
      fail with `NoSuchBucket` otherwise)
- [ ] `CORS_ORIGINS` lists your dashboard origin, comma-separated
      (e.g. `https://attendance.example.com`) — a bare hostname without a
      scheme is rejected at startup
- [ ] Backups: `pgdata`, `miniodata` volumes scheduled
- [ ] Prometheus scraping + alert delivery verified
- [ ] `GET /health` shows `database: ok`; `indexed_users` grows after registering
      faces

## Manual (non-Docker) setup

Follow docs/setup.md for venv-based installs; run gunicorn-style workers with:

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
```

and mirror the nginx snippets in `deployment/nginx/snippets/` for your edge.
