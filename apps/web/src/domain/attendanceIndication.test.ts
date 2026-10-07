import { describe, expect, it } from "vitest";

import type { AttendanceList } from "../api/events";
import {
  aggregatePersonalState,
  attendanceIndication,
  isAttendanceIndicated,
  isAttendanceStaff,
} from "./attendanceIndication";

function attendance(statuses: Array<"present" | "absent" | null>, total = statuses.length): AttendanceList {
  const marked = statuses.filter((status) => status !== null).length;
  return {
    items: statuses.map((status, index) => ({
      person: { id: `p-${index}`, first_name: "Имя", last_name: "Фамилия", middle_name: null },
      status,
      absence_reason: null,
      comment: null,
    })),
    pagination: { page: 1, page_size: 100, total: statuses.length, pages: 1 },
    summary: {
      total,
      marked,
      present: statuses.filter((status) => status === "present").length,
      absent: statuses.filter((status) => status === "absent").length,
      unmarked: total - marked,
    },
  };
}

describe("attendance indication (Issue #305 / ADR-0047 §4-§6)", () => {
  it.each([
    [["present"], "present"],
    [["absent"], "absent"],
    [[null], "unmarked"],
    // Guardian aggregation: present > absent > unmarked.
    [["present", "absent"], "present"],
    [["absent", "present", null], "present"],
    [["absent", null], "absent"],
    [[null, null], "unmarked"],
  ] as const)("aggregates %j to %s", (statuses, expected) => {
    expect(aggregatePersonalState(statuses)).toBe(expected);
  });

  it("gives a Member/Guardian a personal state — unmarked is never absent", () => {
    expect(attendanceIndication(attendance(["present"]), false)).toEqual({
      kind: "personal",
      state: "present",
    });
    expect(attendanceIndication(attendance([null]), false)).toEqual({
      kind: "personal",
      state: "unmarked",
    });
  });

  it("gives staff a marked/total summary instead of one color for a mixed roster", () => {
    const roster = attendance([...Array(10).fill("present"), ...Array(5).fill("absent")], 18);
    expect(attendanceIndication(roster, true)).toEqual({ kind: "summary", marked: 15, total: 18 });
  });

  it("shows nothing without visible attendance rows", () => {
    expect(attendanceIndication(undefined, false)).toBeNull();
    expect(attendanceIndication(attendance([]), false)).toBeNull();
    expect(attendanceIndication(attendance([], 0), true)).toBeNull();
  });

  it("indicates lessons only, never a cancelled one", () => {
    expect(isAttendanceIndicated({ event_type: "lesson", status: "published" })).toBe(true);
    expect(isAttendanceIndicated({ event_type: "lesson", status: "completed" })).toBe(true);
    expect(isAttendanceIndicated({ event_type: "lesson", status: "cancelled" })).toBe(false);
    expect(isAttendanceIndicated({ event_type: "training", status: "published" })).toBe(false);
  });

  it("treats Administrator and Instructor as staff", () => {
    expect(isAttendanceStaff(["admin"])).toBe(true);
    expect(isAttendanceStaff(["instructor"])).toBe(true);
    expect(isAttendanceStaff(["member", "guardian"])).toBe(false);
  });
});
