import { describe, expect, it } from "vitest";

import {
  documentRequirementResultLabel,
  documentStatusIcon,
  documentTypeLabel,
  eventStatusActions,
  eventStatusIcon,
  groupStatusIcon,
  instanceStateIcon,
  instanceStateLabel,
  recordStatusIcon,
  recordStatusLabel,
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

  it("maps inventory record status onto the approved catalog", () => {
    expect(recordStatusIcon("active")).toBe("status.ongoing");
    expect(recordStatusIcon("archived")).toBe("status.archived");
    expect(recordStatusLabel("active")).toBe("Активна");
    expect(recordStatusLabel("archived")).toBe("В архиве");
  });

  it("maps inventory instance states by meaning, never a write-off to the error icon", () => {
    expect(instanceStateIcon("available")).toBe("status.success");
    expect(instanceStateIcon("issued")).toBe("status.ongoing");
    expect(instanceStateIcon("in_repair")).toBe("status.warning");
    expect(instanceStateIcon("written_off")).toBe("status.ended");
    expect(instanceStateIcon("written_off")).not.toBe("status.error");
    expect(instanceStateLabel("available")).toBe("В наличии");
    expect(instanceStateLabel("issued")).toBe("Выдан");
    expect(instanceStateLabel("in_repair")).toBe("В ремонте");
    expect(instanceStateLabel("written_off")).toBe("Списан");
  });
});

describe("eventStatusActions (Issue #281, ADR-0018)", () => {
  it.each([
    ["draft", [["published", false]]],
    ["published", [["in_progress", false], ["cancelled", true]]],
    ["in_progress", [["completed", false], ["cancelled", true]]],
    ["completed", [["archived", false]]],
    ["cancelled", [["archived", false]]],
    ["archived", []],
  ] as const)("offers only the canonical edges from %s", (status, expected) => {
    expect(
      eventStatusActions(status).map((action) => [action.target, action.requiresReason]),
    ).toEqual(expected);
  });
});
