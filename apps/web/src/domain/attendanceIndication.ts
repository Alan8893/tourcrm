/**
 * Calendar attendance indication (ADR-0047 §4-§6, business-rules.md §12).
 *
 * Derived only from the canonical `GET /events/{id}/attendance` response
 * the backend returned for the viewer — never a second, frontend-persisted
 * attendance state, and never a visibility decision: which rows reach the
 * viewer (`self` — their own row, `children` — their accessible children,
 * `all`/`own_*` — the whole roster) is decided by the backend.
 *
 * - Staff (Administrator / Instructor): no global green/red for a mixed
 *   roster — a `marked/total` summary such as `15/18`.
 * - Everyone else (Member, Guardian): one personal state over the visible
 *   rows with the priority `present > absent > unmarked`, so a Member's own
 *   row maps directly and a Guardian sees green when at least one
 *   accessible child was present, red when none was present but one was
 *   absent, and neutral when all are unmarked. Unmarked is never absent.
 */
import type { AttendanceList } from "../api/events";

export type PersonalAttendanceState = "present" | "absent" | "unmarked";

export type AttendanceIndication =
  | { kind: "summary"; marked: number; total: number }
  | { kind: "personal"; state: PersonalAttendanceState };

/** Attendance indication applies to lessons only (ADR-0047). A cancelled
 * occurrence never receives Attendance (ADR-0032 §5). */
export function isAttendanceIndicated(item: { event_type: string; status: string }): boolean {
  return item.event_type === "lesson" && item.status !== "cancelled";
}

export function isAttendanceStaff(roleCodes: readonly string[]): boolean {
  return roleCodes.includes("admin") || roleCodes.includes("instructor");
}

export function aggregatePersonalState(
  statuses: readonly ("present" | "absent" | null)[],
): PersonalAttendanceState {
  if (statuses.includes("present")) return "present";
  if (statuses.includes("absent")) return "absent";
  return "unmarked";
}

export function attendanceIndication(
  attendance: AttendanceList | undefined,
  staff: boolean,
): AttendanceIndication | null {
  // No indication without a well-formed attendance response (e.g. not
  // loaded yet, or not readable by this viewer).
  if (!attendance?.summary || !Array.isArray(attendance.items)) return null;
  if (staff) {
    const { marked, total } = attendance.summary;
    return total > 0 ? { kind: "summary", marked, total } : null;
  }
  if (attendance.items.length === 0) return null;
  return {
    kind: "personal",
    state: aggregatePersonalState(attendance.items.map((item) => item.status)),
  };
}

export function attendanceIndicationLabel(indication: AttendanceIndication): string {
  if (indication.kind === "summary") {
    return `Отмечено ${indication.marked} из ${indication.total}`;
  }
  switch (indication.state) {
    case "present":
      return "Посещаемость: присутствовал";
    case "absent":
      return "Посещаемость: отсутствовал";
    default:
      return "Посещаемость: не отмечено";
  }
}
