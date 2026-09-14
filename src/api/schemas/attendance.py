from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class AttendanceMethod(StrEnum):
    auto = "auto"
    manual = "manual"
    kiosk = "kiosk"


class MarkAttendanceRequest(BaseModel):
    user_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    device_id: str | None = Field(None, max_length=100)
    method: AttendanceMethod = Field(default=AttendanceMethod.auto)
    is_spoof: bool = Field(default=False)
    metadata: dict = Field(default_factory=dict)


class ManualMarkRequest(BaseModel):
    user_ids: list[UUID] = Field(min_length=1, max_length=500)
    timestamp: datetime | None = None
    device_id: str | None = Field(None, max_length=100)
    method: AttendanceMethod = Field(default=AttendanceMethod.manual)
    note: str | None = Field(None, max_length=500)


class AttendanceQueryFilters(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    user_id: UUID | None = None
    department_id: UUID | None = None
    method: AttendanceMethod | None = None
    is_spoof: bool | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class AttendanceResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_name: str
    user_external_id: str | None
    timestamp: datetime
    method: str
    confidence: float | None
    is_spoof: bool
    device_id: str | None
    department_name: str | None

    model_config = {"from_attributes": True}


class AttendanceListResponse(BaseModel):
    records: list[AttendanceResponse]
    total: int
    page: int
    page_size: int


class AttendanceStatsResponse(BaseModel):
    date: date
    total_records: int
    unique_users: int
    present_users: int
    absent_users: int
    by_department: dict[str, int]
    by_method: dict[str, int]
    spoof_attempts: int


class DailyAttendanceSummary(BaseModel):
    date: date
    total: int
    present: int
    absent: int
    late: int
    on_time: int


class ExportFormat(StrEnum):
    csv = "csv"
    excel = "excel"


class AttendanceExportRequest(BaseModel):
    date_from: date
    date_to: date
    format: ExportFormat = Field(default=ExportFormat.csv)
    department_id: UUID | None = None
    include_metadata: bool = Field(default=False)
