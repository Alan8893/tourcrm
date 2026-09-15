import { describe, expect, it } from "vitest";

import { eventStatusIcon, groupStatusIcon } from "./statusMapping";

describe("statusMapping", () => {
  it("never maps a cancelled event to the error status icon (ASSET-STATUS.md)", () => {
    expect(eventStatusIcon("cancelled")).not.toBe("status.error");
    expect(eventStatusIcon("cancelled")).toBe("status.ended");
  });

  it("maps event lifecycle states onto their closest approved semantic meaning", () => {
    expect(eventStatusIcon("draft")).toBe("status.planned");
    expect(eventStatusIcon("published")).toBe("status.planned");
    expect(eventStatusIcon("in_progress")).toBe("status.ongoing");
    expect(eventStatusIcon("completed")).toBe("status.completed");
    expect(eventStatusIcon("archived")).toBe("status.archived");
  });

  it("maps group status onto the approved catalog", () => {
    expect(groupStatusIcon("active")).toBe("status.ongoing");
    expect(groupStatusIcon("archived")).toBe("status.archived");
  });
});
