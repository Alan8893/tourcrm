import { describe, expect, it } from "vitest";

import { NAVIGATION_ITEMS, visibleNavigationItems } from "./navigation";

function labelsFor(...roleCodes: string[]) {
  return visibleNavigationItems(roleCodes.map((role_code) => ({ role_code }))).map(
    (item) => item.label,
  );
}

describe("visibleNavigationItems (TH-0120 role-aware navigation, UNION)", () => {
  it("keeps the canonical seven-item catalog unchanged", () => {
    expect(NAVIGATION_ITEMS.map((item) => item.label)).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Настройки",
    ]);
  });

  it("Administrator sees all seven sections", () => {
    expect(labelsFor("admin")).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Настройки",
    ]);
  });

  it("Instructor sees everything except Отчёты", () => {
    expect(labelsFor("instructor")).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Настройки",
    ]);
  });

  it("Member sees Главная, Группы, События, Достижения, Настройки — not Люди or Отчёты", () => {
    const labels = labelsFor("member");
    expect(labels).toEqual(["Главная", "Группы", "События", "Достижения", "Настройки"]);
    expect(labels).not.toContain("Люди");
    expect(labels).not.toContain("Отчёты");
  });

  it("Guardian sees Главная, События, Достижения, Настройки — not Люди, Группы or Отчёты", () => {
    const labels = labelsFor("guardian");
    expect(labels).toEqual(["Главная", "События", "Достижения", "Настройки"]);
    expect(labels).not.toContain("Люди");
    expect(labels).not.toContain("Группы");
    expect(labels).not.toContain("Отчёты");
  });

  it("multi-role users get the UNION of their roles' sections, in canonical order", () => {
    // Guardian adds nothing beyond Member; Member ∪ Guardian = Member's set.
    expect(labelsFor("guardian", "member")).toEqual([
      "Главная",
      "Группы",
      "События",
      "Достижения",
      "Настройки",
    ]);
    // Instructor ∪ Guardian: Люди comes from Instructor only.
    expect(labelsFor("guardian", "instructor")).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Настройки",
    ]);
    // Instructor ∪ Admin: Отчёты comes from Admin only.
    expect(labelsFor("instructor", "admin")).toEqual(labelsFor("admin"));
  });

  it("is independent of assignment order and duplicate assignments", () => {
    expect(labelsFor("member", "guardian", "member")).toEqual(labelsFor("guardian", "member"));
  });

  it("grants nothing for a role code outside the canonical matrix", () => {
    expect(labelsFor("unknown_role")).toEqual([]);
    expect(labelsFor("unknown_role", "guardian")).toEqual(labelsFor("guardian"));
  });
});
