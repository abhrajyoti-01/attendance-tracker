from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DashboardSummaryResponse(BaseModel):
    total_users: int
    registered_users: int
    unregistered_users: int
    active_users: int
    present_today: int
    absent_today: int
    total_attendance_today: int
    spoof_attempts_today: int
    last_updated: datetime


class DailyChartPoint(BaseModel):
    date: date
    present: int
    absent: int
    total: int


class DailyAttendanceChartResponse(BaseModel):
    data: list[DailyChartPoint]
    start_date: date
    end_date: date


class DepartmentStats(BaseModel):
    department_id: UUID
    department_name: str
    total_users: int
    present_today: int
    absent_today: int
    attendance_rate: float


class DepartmentChartResponse(BaseModel):
    data: list[DepartmentStats]
    total_departments: int


class HourlyAttendancePoint(BaseModel):
    hour: int
    check_ins: int


class HourlyChartResponse(BaseModel):
    data: list[HourlyAttendancePoint]
    date: date


class AttendanceFeedEvent(BaseModel):
    id: str
    user_id: UUID
    user_name: str
    user_external_id: str | None
    timestamp: datetime
    method: str
    confidence: float | None
    is_spoof: bool
    department_name: str | None


class OrganizationSettingsResponse(BaseModel):
    working_hours_start: str = "09:00"
    working_hours_end: str = "17:00"
    allow_late_checkin: bool = True
    recognition_threshold: float = 0.55
    require_liveness_check: bool = True
    liveness_threshold: float = 0.67
    max_users: int


class OrganizationSettingsUpdate(BaseModel):
    working_hours_start: str | None = None
    working_hours_end: str | None = None
    allow_late_checkin: bool | None = None
    recognition_threshold: float | None = Field(None, ge=0.0, le=1.0)
    require_liveness_check: bool | None = None
    liveness_threshold: float | None = Field(None, ge=0.0, le=1.0)
