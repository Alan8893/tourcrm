import { describe, expect, it } from "vitest";

import {
  addDays,
  addMonths,
  buildMonthGrid,
  formatDateParam,
  isSameDay,
  monthLabel,
  monthRange,
  parseDateParam,
  startOfDay,
  toDatetimeLocalValue,
} from "./calendarDate";

describe("formatDateParam / parseDateParam", () => {
  it("round-trips a local date through the URL param format", () => {
    const date = new Date(2026, 2, 5); // 5 March 2026, local time
    expect(formatDateParam(date)).toBe("2026-03-05");
    const parsed = parseDateParam("2026-03-05");
    expect(parsed && isSameDay(parsed, date)).toBe(true);
  });

  it("rejects malformed or non-existent dates", () => {
    expect(parseDateParam(null)).toBeNull();
    expect(parseDateParam("")).toBeNull();
    expect(parseDateParam("not-a-date")).toBeNull();
    expect(parseDateParam("2026-13-01")).toBeNull();
    expect(parseDateParam("2026-02-30")).toBeNull(); // 2026 is not a leap year
  });
});

describe("addMonths", () => {
  it("moves forward and backward by whole months", () => {
    const date = new Date(2026, 0, 15); // 15 Jan 2026
    expect(formatDateParam(addMonths(date, 1))).toBe("2026-02-15");
    expect(formatDateParam(addMonths(date, -1))).toBe("2025-12-15");
  });

  it("clamps the day instead of overflowing into the next month", () => {
    const jan31 = new Date(2026, 0, 31);
    // Feb 2026 has 28 days.
    expect(formatDateParam(addMonths(jan31, 1))).toBe("2026-02-28");
  });
});

describe("monthRange", () => {
  it("produces a [from,to) instant pair spanning exactly the calendar month", () => {
    const { from, to } = monthRange(new Date(2026, 2, 15)); // March 2026
    const fromDate = new Date(from);
    const toDate = new Date(to);
    expect(fromDate.getMonth()).toBe(2);
    expect(fromDate.getDate()).toBe(1);
    expect(fromDate.getHours()).toBe(0);
    expect(toDate.getMonth()).toBe(3); // April 1st, exclusive upper bound
    expect(toDate.getDate()).toBe(1);
    expect(toDate.getTime()).toBeGreaterThan(fromDate.getTime());
  });
});

describe("buildMonthGrid", () => {
  it("returns a 42-day Monday-start grid covering the month", () => {
    const grid = buildMonthGrid(new Date(2026, 2, 15)); // March 2026
    expect(grid).toHaveLength(42);
    // 1 March 2026 is a Sunday, so the grid should start Monday 23 Feb.
    expect(formatDateParam(grid[0].date)).toBe("2026-02-23");
    expect(grid[0].date.getDay()).toBe(1); // Monday
    const marchDays = grid.filter((day) => day.inCurrentMonth);
    expect(marchDays).toHaveLength(31);
    expect(formatDateParam(marchDays[0].date)).toBe("2026-03-01");
    expect(formatDateParam(marchDays[marchDays.length - 1].date)).toBe("2026-03-31");
  });

  it("marks today's cell", () => {
    const today = new Date(2026, 2, 10);
    const grid = buildMonthGrid(new Date(2026, 2, 1), today);
    const todayCell = grid.find((day) => day.isToday);
    expect(todayCell && formatDateParam(todayCell.date)).toBe("2026-03-10");
  });

  it("does not mark any day as today when today falls outside the grid", () => {
    const grid = buildMonthGrid(new Date(2026, 2, 1), new Date(2027, 0, 1));
    expect(grid.some((day) => day.isToday)).toBe(false);
  });
});

describe("monthLabel", () => {
  it("renders a Russian month name and year", () => {
    expect(monthLabel(new Date(2026, 8, 1))).toBe("Сентябрь 2026");
  });
});

describe("startOfDay / isSameDay / addDays", () => {
  it("zeroes the time component", () => {
    const date = new Date(2026, 5, 1, 14, 30, 15);
    const start = startOfDay(date);
    expect(start.getHours()).toBe(0);
    expect(start.getMinutes()).toBe(0);
    expect(isSameDay(start, date)).toBe(true);
  });

  it("adds days across month boundaries", () => {
    const date = new Date(2026, 1, 27); // 27 Feb 2026
    expect(formatDateParam(addDays(date, 3))).toBe("2026-03-02");
  });
});

describe("toDatetimeLocalValue", () => {
  it("formats a local date/time for <input type=datetime-local>", () => {
    const date = new Date(2026, 8, 5, 9, 5);
    expect(toDatetimeLocalValue(date)).toBe("2026-09-05T09:05");
  });

  it("round-trips through the native Date parser", () => {
    const date = new Date(2026, 8, 5, 17, 45);
    const roundTripped = new Date(toDatetimeLocalValue(date));
    expect(roundTripped.getTime()).toBe(date.getTime());
  });
});
