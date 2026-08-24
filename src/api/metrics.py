from prometheus_client import Counter, Gauge, Histogram

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
)

model_inferences_total = Counter(
    "model_inferences_total",
    "Total model inferences",
    ["status"],
)

matches_total = Counter(
    "matches_total",
    "Face matching outcomes",
    ["result"],
)

liveness_checks_total = Counter(
    "liveness_checks_total",
    "Liveness check outcomes",
    ["result"],
)

attendance_events_total = Counter(
    "attendance_events_total",
    "Attendance events recorded",
    ["method", "result"],
)

db_query_duration = Histogram(
    "db_query_duration_seconds",
    "Database query duration",
    ["operation"],
)

matcher_index_size = Gauge(
    "matcher_index_size",
    "Number of embeddings in the in-memory matcher index",
    ["organization_id"],
)
