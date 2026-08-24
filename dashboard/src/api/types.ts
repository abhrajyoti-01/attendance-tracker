/** Response shapes mirroring the FastAPI schemas. */

export interface UserProfile {
  id: string;
  name: string;
  external_id: string | null;
  email: string | null;
  phone: string | null;
  department_id: string | null;
  department_name: string | null;
  role: string;
  is_active: boolean;
  is_registered: boolean;
  registration_quality: number | null;
  created_at: string;
}

export interface DashboardSummary {
  total_users: number;
  registered_users: number;
  unregistered_users: number;
  active_users: number;
  present_today: number;
  absent_today: number;
  total_attendance_today: number;
  spoof_attempts_today: number;
  last_updated: string;
}

export interface DailyChartPoint {
  date: string;
  present: number;
  absent: number;
  total: number;
}

export interface DailyChart {
  data: DailyChartPoint[];
  start_date: string;
  end_date: string;
}

export interface HourlyPoint {
  hour: number;
  check_ins: number;
}

export interface HourlyChart {
  data: HourlyPoint[];
  date: string;
}

export interface DepartmentStats {
  department_id: string;
  department_name: string;
  total_users: number;
  present_today: number;
  absent_today: number;
  attendance_rate: number;
}

export interface DepartmentChart {
  data: DepartmentStats[];
  total_departments: number;
}

export interface FeedEvent {
  id: string;
  user_id: string;
  user_name: string;
  user_external_id: string | null;
  timestamp: string;
  method: string;
  confidence: number | null;
  is_spoof: boolean;
  department_name: string | null;
}

export interface FeedResponse {
  events: FeedEvent[];
}

export interface UserList {
  users: UserProfile[];
  total: number;
  page: number;
  page_size: number;
}

export interface Department {
  id: string;
  name: string;
  parent_id: string | null;
  user_count: number;
  created_at: string;
}

export interface DepartmentList {
  departments: Department[];
  total: number;
}

export interface OrgSettings {
  working_hours_start: string;
  working_hours_end: string;
  allow_late_checkin: boolean;
  recognition_threshold: number;
  require_liveness_check: boolean;
  liveness_threshold: number;
  max_users: number;
}
