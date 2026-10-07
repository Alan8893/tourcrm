import { describe, expect, it } from "vitest";

import { canManageEvents, eventDetailTabIds } from "./eventAccess";

describe("eventAccess — role-aware Event UI (Issue #299)", () => {
  it.each([
    [["admin"], true],
    [["instructor"], true],
    [["member"], false],
    [["guardian"], false],
    [["member", "guardian"], false],
    [["guardian", "instructor"], true],
    [[], false],
    [["unknown"], false],
  ])("canManageEvents(%j) is %s", (roles, expected) => {
    expect(canManageEvents(roles)).toBe(expected);
  });

  it("offers «Участники» to Administrator and Instructor for an ordinary Event", () => {
    expect(eventDetailTabIds(["admin"], "event")).toEqual(["overview", "participants"]);
    expect(eventDetailTabIds(["instructor"], "event")).toEqual(["overview", "participants"]);
    expect(eventDetailTabIds(["member", "admin"], "event")).toEqual(["overview", "participants"]);
  });

  it("never offers the general roster to Member or Guardian", () => {
    expect(eventDetailTabIds(["member"], "event")).toEqual(["overview"]);
    expect(eventDetailTabIds(["guardian"], "event")).toEqual(["overview"]);
    expect(eventDetailTabIds(["member", "guardian"], "event")).toEqual(["overview"]);
  });

  it("offers no roster for a recurring occurrence, whatever the role", () => {
    expect(eventDetailTabIds(["admin"], "occurrence")).toEqual(["overview"]);
    expect(eventDetailTabIds(["instructor"], "occurrence")).toEqual(["overview"]);
  });

  it("offers «Посещаемость» to staff for a lesson only (Issue #305)", () => {
    expect(eventDetailTabIds(["admin"], "event", "lesson")).toEqual([
      "overview",
      "participants",
      "attendance",
    ]);
    expect(eventDetailTabIds(["instructor"], "occurrence", "lesson")).toEqual([
      "overview",
      "attendance",
    ]);
    expect(eventDetailTabIds(["admin"], "event", "training")).toEqual(["overview", "participants"]);
    expect(eventDetailTabIds(["member"], "event", "lesson")).toEqual(["overview"]);
    expect(eventDetailTabIds(["guardian"], "event", "lesson")).toEqual(["overview"]);
  });
});
