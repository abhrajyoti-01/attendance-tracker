# API Overview

Base URL: `https://<host>` (nginx) or `http://api-host:8000` directly.
Interactive OpenAPI docs at `/docs` when enabled outside production.

## Authentication

Two identity types:

| caller | mechanism | notes |
| --- | --- | --- |
| users | `Authorization: Bearer <access>` | 30-min JWT + rotating refresh token |
| machines/kiosks | `X-API-Key: ak_...` | scoped keys; shown once at creation |

Access tokens carry the user's `token_version`. Logout, password change and an
admin password reset bump that version, so *all* previously issued access tokens
are rejected immediately with 401 rather than staying valid until expiry.

All responses carry `X-Correlation-ID`; include it in bug reports.

### Endpoints

```
POST   /api/auth/login                     {email, password, organization_slug}
POST   /api/auth/refresh                   {refresh_token}      -> rotated pair
POST   /api/auth/logout                    {refresh_token}      -> revokes sessions
POST   /api/auth/forgot-password           {email, organization_slug}  (202 always)
POST   /api/auth/reset-password            {token, new_password}
POST   /api/auth/change-password           {current_password, new_password}
```

Replaying an already-rotated refresh token revokes every session of that user
(theft detection) and returns 401. Forgot-password always answers `202` with an
identical body — an unknown organization and an unknown user are
indistinguishable. The reset link is delivered by SMTP only.

Login failures are indistinguishable too: a wrong password, an unknown account
and a disabled account all return `401 Invalid credentials`.

## Users (`/api/users`, org-admin)

```
POST   /api/users                          create (optional initial password/invite)
GET    /api/users                          list: search, department, active, registered filters
GET    /api/users/me                       own profile (any role)
GET    /api/users/{id}                     member: self only; admin: same org; superadmin: any
PATCH  /api/users/{id}                     partial update (duplicate checks on ids/email)
DELETE /api/users/{id}                     soft-deactivate (self-delete blocked)
POST   /api/users/bulk-import              <=1000 rows, per-row errors via SAVEPOINT
POST   /api/users/{id}/password            admin-set password (revokes sessions)
```

Roles validated against `superadmin | org_admin | member`. Org limits enforced.
An org admin cannot create `org_admin`/`superadmin` accounts, and cannot modify,
deactivate or reset the password of a superadmin — only a superadmin can.

## Departments (`/api/departments`)

`GET` list with user counts · `POST` create · `PATCH` rename/reparent ·
`DELETE` (blocked while users assigned). Org-admin.

## Recognition (`/api/recognize`)

```
POST  /api/recognize                       {image_base64, liveness_frames_base64[],
                                            threshold, check_liveness, auto_mark_attendance}
POST  /api/recognize/batch                 up to 10 frames; same liveness rules
POST  /api/recognize/users/{id}/register   3-5 frames, quality-gated (org-admin)
DELETE /api/recognize/users/{id}/register  remove template
GET   /api/recognize/users/{id}/register/status
```

Matching is restricted to the caller's organization index. Liveness failures are
recorded in `spoof_attempts` and never mark attendance. Threshold overrides are
floored at `MATCH_THRESHOLD_FLOOR`.

**Liveness is temporal.** Blink and head-movement cannot be measured from one
frame, so a single-frame request is scored on static LBP texture only and
returns `liveness_details` beginning `static_only`. Send
`liveness_frames_base64` (a short burst of the same person) to evaluate all
available checks; set `SPOOF_REQUIRE_SEQUENCE=true` to reject single-frame
requests outright.

Thresholds and the liveness requirement are read per organization from
`/api/dashboard/settings`, falling back to the global defaults.

## Attendance (`/api/attendance`)

```
POST  /api/attendance                      machine marking; requires API key with attendance:write;
                                            scoped to the key's organization; dedupe window applies
POST  /api/attendance/manual               bulk manual marking (org-admin)
GET   /api/attendance                      filters: dates, user, department, method, is_spoof
GET   /api/attendance/today
GET   /api/attendance/stats                SQL aggregates incl. by_department/by_method
POST  /api/attendance/export               sync CSV/XLSX (<=366 days, <=50k rows)
POST  /api/attendance/export/async         Celery job -> MinIO presigned URL (202)
GET   /api/attendance/methods              valid method values
```

`method` must be one of `auto | manual | kiosk`; anything else is a `422`.
`format` must be `csv` or `excel`. Exported cells are sanitized against
spreadsheet formula injection.

## Dashboard (`/api/dashboard`, any authenticated user; stream is org-admin)

```
GET   /api/dashboard/summary               counts incl. spoof attempts today
GET   /api/dashboard/chart/daily?days=N    present/absent/total per day
GET   /api/dashboard/chart/department      per-department rates (single aggregate query)
GET   /api/dashboard/chart/hours           24-hour check-in histogram
GET   /api/dashboard/feed                  latest N events with names/departments
GET   /api/dashboard/settings              merged org settings with defaults
PATCH /api/dashboard/settings              org-admin; HH:MM validation on hours
GET   /api/dashboard/stream                SSE live feed (org-admin; ENABLE_LIVE_FEED)
```

`recognition_threshold` and `liveness_threshold` set via `PATCH` are applied to
subsequent `/api/recognize` calls for that organization. The recognition
threshold is clamped server-side to `MATCH_THRESHOLD_FLOOR`. The SSE endpoint
authenticates without holding a pooled database connection for the stream's
lifetime.

## Admin (`/api/admin`)

```
GET/POST          /api/admin/organizations          superadmin
GET/PATCH         /api/admin/organizations/{id}     superadmin
GET/POST          /api/admin/api-keys               org-admin; plaintext returned once
DELETE            /api/admin/api-keys/{id}          revoke
POST/GET          /api/admin/training-jobs          superadmin; queue fine-tuning
GET               /api/admin/training-jobs/{id}     status/metrics/checkpoint paths
```

## System

```
GET /health     db critical; redis/model degrade; 503 when db fails
GET /metrics    Prometheus exposition
```

## Error responses

```json
{"detail": "human-readable message"}
```

422 validation errors return pydantic error objects without non-serializable
context. 429 includes `Retry-After`.

## Rate limiting (application level)

| scope | limit |
| --- | --- |
| `/api/auth/*` | 10/min/IP |
| `/api/recognize/*` | 60/min/IP |
| other `/api/*` | 100/min/IP |

Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`. nginx adds its own zones
in front.
