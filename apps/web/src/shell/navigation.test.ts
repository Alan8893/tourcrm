import { describe, expect, it } from "vitest";

import { NAVIGATION_ITEMS, isNavigationItemVisible, visibleNavigationItems } from "./navigation";

function labelsFor(...roleCodes: string[]) {
  return visibleNavigationItems(roleCodes.map((role_code) => ({ role_code }))).map(
    (item) => item.label,
  );
}

describe("visibleNavigationItems (TH-0120 role-aware navigation, UNION)", () => {
  it("keeps the canonical eight-item catalog unchanged («Склад» between Отчёты and Настройки)", () => {
    expect(NAVIGATION_ITEMS.map((item) => item.label)).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Склад",
      "Настройки",
    ]);
    expect(NAVIGATION_ITEMS.find((item) => item.id === "inventory")).toMatchObject({
      path: "/inventory",
      icon: "nav.inventory",
    });
  });

  it("Administrator sees all eight sections", () => {
    expect(labelsFor("admin")).toEqual([
      "Главная",
      "Люди",
      "Группы",
      "События",
      "Достижения",
      "Отчёты",
      "Склад",
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

describe("isNavigationItemVisible (Issue #212)", () => {
  const roles = (...codes: string[]) => codes.map((role_code) => ({ role_code }));

  it("Reports is visible only when an Administrator assignment is present", () => {
    expect(isNavigationItemVisible(roles("admin"), "reports")).toBe(true);
    expect(isNavigationItemVisible(roles("instructor"), "reports")).toBe(false);
    expect(isNavigationItemVisible(roles("member"), "reports")).toBe(false);
    expect(isNavigationItemVisible(roles("guardian"), "reports")).toBe(false);
    expect(isNavigationItemVisible(roles("instructor", "member", "guardian"), "reports")).toBe(false);
    expect(isNavigationItemVisible(roles("guardian", "admin"), "reports")).toBe(true);
  });

  it("Склад is visible only when an Administrator assignment is present", () => {
    expect(isNavigationItemVisible(roles("admin"), "inventory")).toBe(true);
    expect(isNavigationItemVisible(roles("instructor"), "inventory")).toBe(false);
    expect(isNavigationItemVisible(roles("member"), "inventory")).toBe(false);
    expect(isNavigationItemVisible(roles("guardian"), "inventory")).toBe(false);
    expect(isNavigationItemVisible(roles("instructor", "member", "guardian"), "inventory")).toBe(false);
    expect(isNavigationItemVisible(roles("guardian", "admin"), "inventory")).toBe(true);
  });

  it("Achievements is visible for every canonical role", () => {
    for (const role of ["admin", "instructor", "member", "guardian"]) {
      expect(isNavigationItemVisible(roles(role), "achievements")).toBe(true);
    }
  });

  it("uses UNION across roles and grants nothing without a matrix role", () => {
    expect(isNavigationItemVisible(roles("guardian"), "groups")).toBe(false);
    expect(isNavigationItemVisible(roles("guardian", "member"), "groups")).toBe(true);
    expect(isNavigationItemVisible(roles("member"), "people")).toBe(false);
    expect(isNavigationItemVisible(roles("member", "instructor"), "people")).toBe(true);
    expect(isNavigationItemVisible([], "home")).toBe(false);
  });
});
