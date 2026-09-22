import { describe, expect, it } from "vitest";

import {
  documentRequirementResultLabel,
  documentStatusIcon,
  documentTypeLabel,
  eventStatusIcon,
  groupStatusIcon,
} from "./statusMapping";

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

  it("never maps a revoked document to the error status icon (revocation is a business outcome)", () => {
    expect(documentStatusIcon("revoked")).not.toBe("status.error");
    expect(documentStatusIcon("revoked")).toBe("status.archived");
  });

  it("gives medical_certificate the dedicated Issue #175 label, and passes through any other type", () => {
    expect(documentTypeLabel("medical_certificate")).toBe("Медицинская справка");
    expect(documentTypeLabel("insurance_waiver")).toBe("insurance_waiver");
  });

  it("labels the three canonical EventDocumentRequirement check results distinctly", () => {
    expect(documentRequirementResultLabel("valid")).toBe("Действителен");
    expect(documentRequirementResultLabel("missing")).toBe("Отсутствует");
    expect(documentRequirementResultLabel("expired")).toBe("Истёк");
  });
});
