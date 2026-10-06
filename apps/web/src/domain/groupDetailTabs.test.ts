import { describe, expect, it } from "vitest";

import { groupDetailTabIds } from "./groupDetailTabs";

describe("groupDetailTabIds (Issue #285)", () => {
  it("gives an administrator overview, participants and schedule", () => {
    expect(groupDetailTabIds(["admin"])).toEqual(["overview", "members", "schedule"]);
  });

  it("gives an instructor participants and schedule, never the overview", () => {
    expect(groupDetailTabIds(["instructor"])).toEqual(["members", "schedule"]);
  });

  it("gives a member only the schedule", () => {
    expect(groupDetailTabIds(["member"])).toEqual(["schedule"]);
  });

  it("gives the union to a user holding several roles", () => {
    expect(groupDetailTabIds(["member", "instructor"])).toEqual(["members", "schedule"]);
    expect(groupDetailTabIds(["member", "admin"])).toEqual(["overview", "members", "schedule"]);
  });

  it("falls back to the schedule only when no role is known", () => {
    expect(groupDetailTabIds([])).toEqual(["schedule"]);
  });
});
