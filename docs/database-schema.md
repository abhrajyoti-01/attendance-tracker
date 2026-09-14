# Database Schema

13 tables. Managed exclusively through Alembic (`migrations/`; `0001_baseline`
then `0002_token_version`). The API verifies at startup that the applied
revision matches the current head and refuses to serve otherwise.

All timestamps are `timestamptz` (UTC). UUID primary keys throughout.
`organizations` cascades to all tenant-owned rows; `users.id` references use
`SET NULL` where history must survive user deletion.

## organizations

| column | type | notes |
| --- | --- | --- |
| id | uuid PK | |
| name | varchar(255) NOT NULL | |
| slug | varchar(100) UNIQUE NOT NULL | login identifier |
| logo_url | text NULL | |
| settings | jsonb/json NOT NULL | working hours, thresholds, liveness policy |
| max_users | int NOT NULL default 5000 | enforced on user creation |
| is_active | bool NOT NULL | inactive orgs cannot authenticate |

## departments

Two-level hierarchy via self-referencing `parent_id` (`SET NULL`).
Unique `(organization_id, name)`.

## users

| column | type | notes |
| --- | --- | --- |
| organization_id | uuid FK CASCADE | tenant scope |
| external_id | varchar(100) NULL | unique per org (`ix_user_org_external`) |
| name / phone | varchar NOT NULL / NULL | |
| email | varchar(255) NULL | partial unique index per org where email IS NOT NULL; normalized lowercase |
| department_id | uuid FK departments SET NULL | |
| role | varchar(50) | `superadmin` \| `org_admin` \| `member` (validated in schemas) |
| password_hash | text NULL | bcrypt; NULL = cannot sign in until set |
| is_active | bool | deactivation revokes matcher entry |
| token_version | int NOT NULL default 0 | bumped on logout/password change; access tokens carry it and stale ones are rejected |
| metadata | json | renamed attribute `metadata_` (SQLAlchemy reserves `.metadata`) |

## embeddings

One active template per user.

| column | notes |
| --- | --- |
| user_id FK users CASCADE UNIQUE | |
| vector LargeBinary | float32 little-endian, dim = model output (512) |
| num_samples SmallInteger | images averaged into the template |
| quality_score Float NULL | mean of accepted samples |
| model_version varchar(100) | source ONNX filename for dimension-mismatch detection |
| version Integer | bumped on re-registration |

The in-memory matcher decodes vectors with a strict dimension check and skips
incompatible rows (logged), so swapping models never crashes recognition.

## face_images

Reserved provenance table for registration samples (`storage_key`,
`quality_score`, optional embedding bytes, `captured_at`).

> **Not currently written.** Face crops are processed in memory and discarded;
> only the aggregated embedding is persisted. MinIO holds attendance export
> files, not face images. This table exists for a future image-retention
> feature.

## attendance

Indexes `(organization_id, timestamp)` and `(user_id, timestamp)` feed every
dashboard query. `method ∈ {auto, manual, kiosk}`; spoof-marked rows are
excluded from presence counts. Duplicate suppression uses a rolling window
(`ATTENDANCE_DEDUPE_WINDOW_MINUTES`, default 10).

## spoof_attempts

Liveness failures: reason, liveness score, optional snapshot key. The snapshot
column is reserved and currently always NULL (no image bytes are stored).
Purged after 90 days by the `purge_old_spoof_attempts_task` beat job.

## audit_log

Append-only security trail: action, actor, target type/id, details JSON,
source IP. The request-scoped session commits on clean exit, so audit rows
written by a handler are persisted even when the handler does not commit
explicitly.

## api_keys

Machine credentials: HMAC-SHA256 `key_hash` (domain-separated), display `prefix`,
scope list JSON, `created_by`, expiry, revocation flag. Plaintext shown once.

## refresh_tokens

Server-side rotation ledger keyed by token `jti`: `expires_at`, `revoked_at`,
`replaced_by_jti`, issuing IP. Rotation marks the presented row revoked;
replay of a revoked row triggers revocation of every active session for the user.

## password_reset_tokens

HMAC-hashed one-time tokens with expiry/usage tracking and requesting IP.
Consumption sets the new bcrypt hash and revokes all refresh sessions.

## notification_preferences

Per-user digest/slack settings (reserved for the notification worker).

## training_jobs

Fine-tuning lifecycle: status machine `pending → running → completed|failed`,
config JSON, metrics JSON (eval + data stats), checkpoint path, ONNX path,
Celery task id.
