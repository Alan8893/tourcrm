import { describe, expect, it } from "vitest";

import type { InventoryStorageLocation } from "../api/inventory";
import {
  INSTANCE_STATE_OPTIONS,
  accountingModeLabel,
  buildLocationTree,
  formatCostMinor,
  formatQuantity,
  inventoryTabHref,
  isInventoryTab,
  locationPath,
  minorToRublesInput,
  movementTypeLabel,
  parseQuantity,
  parseRublesToMinor,
} from "./inventoryFormat";

/** Intl's ru-RU output separates groups and the currency sign with a
 * (narrow) no-break space; compare on plain spaces. */
const plain = (value: string) => value.replace(/[\u00a0\u202f]/g, " ");

function location(
  id: string,
  parent_id: string | null,
  name: string,
  status: "active" | "archived" = "active",
): InventoryStorageLocation {
  return { id, parent_id, name, status };
}

function byId(locations: InventoryStorageLocation[]) {
  return new Map(locations.map((entry) => [entry.id, entry]));
}

describe("formatCostMinor", () => {
  it("formats whole kopecks as RUB", () => {
    expect(plain(formatCostMinor(12050))).toBe("120,50 ₽");
    expect(plain(formatCostMinor(123456789))).toBe("1 234 567,89 ₽");
    expect(plain(formatCostMinor(0))).toBe("0,00 ₽");
  });

  it("reports a missing cost", () => {
    expect(formatCostMinor(null)).toBe("не указана");
  });
});

describe("formatQuantity", () => {
  it("groups digits the Russian way and appends the unit", () => {
    expect(plain(formatQuantity(1234567, "м"))).toBe("1 234 567 м");
    expect(plain(formatQuantity(40, "шт"))).toBe("40 шт");
  });

  it("omits an unknown unit", () => {
    expect(plain(formatQuantity(1500))).toBe("1 500");
  });
});

describe("locationPath", () => {
  const locations = byId([
    location("root", null, "Склад клуба"),
    location("rack", "root", "Стеллаж А"),
    location("shelf", "rack", "Полка 2"),
  ]);

  it("joins the ancestors from the root down", () => {
    expect(locationPath("shelf", locations)).toBe("Склад клуба › Стеллаж А › Полка 2");
    expect(locationPath("root", locations)).toBe("Склад клуба");
  });

  it("handles a missing or unknown location", () => {
    expect(locationPath(null, locations)).toBe("—");
    expect(locationPath("nope", locations)).toBe("Неизвестное место");
  });

  it("stops on a parent cycle instead of looping forever", () => {
    const cyclic = byId([location("a", "b", "А"), location("b", "a", "Б")]);
    expect(locationPath("a", cyclic)).toBe("Б › А");
  });
});

describe("buildLocationTree", () => {
  it("nests children under parents and sorts each level by name", () => {
    const tree = buildLocationTree([
      location("shelf-b", "rack", "Полка Б"),
      location("rack", "root", "Стеллаж"),
      location("root", null, "Склад"),
      location("shelf-a", "rack", "Полка А"),
      location("garage", null, "Гараж"),
    ]);

    expect(tree.map((node) => node.location.name)).toEqual(["Гараж", "Склад"]);
    const rack = tree[1].children[0];
    expect(rack.location.name).toBe("Стеллаж");
    expect(rack.children.map((node) => node.location.name)).toEqual(["Полка А", "Полка Б"]);
  });

  it("puts a node whose parent is not in the list at the top level", () => {
    const tree = buildLocationTree([location("shelf", "rack-missing", "Полка", "archived")]);
    expect(tree).toHaveLength(1);
    expect(tree[0].location.parent_id).toBe("rack-missing");
  });

  it("returns an empty tree for no locations", () => {
    expect(buildLocationTree([])).toEqual([]);
  });
});

describe("inventory tabs", () => {
  it("recognises only the /inventory tabs", () => {
    for (const tab of ["items", "locations", "stock", "instances", "issues", "references"]) {
      expect(isInventoryTab(tab)).toBe(true);
    }
    expect(isInventoryTab("movements")).toBe(false);
    expect(isInventoryTab(null)).toBe(false);
    expect(isInventoryTab(undefined)).toBe(false);
  });

  it("links «Номенклатура» without a parameter and the other tabs with ?tab=", () => {
    expect(inventoryTabHref("items")).toBe("/inventory");
    expect(inventoryTabHref("stock")).toBe("/inventory?tab=stock");
    expect(inventoryTabHref("instances")).toBe("/inventory?tab=instances");
  });
});

describe("labels", () => {
  it("names both accounting modes", () => {
    expect(accountingModeLabel("quantity")).toBe("Количественный учёт");
    expect(accountingModeLabel("instance")).toBe("Поэкземплярный учёт");
  });

  it("uses the canonical movement names (inventory.md §13)", () => {
    expect(movementTypeLabel("receipt")).toBe("Приём");
    expect(movementTypeLabel("write_off")).toBe("Списание");
    expect(movementTypeLabel("writeoff_reversal")).toBe("Отмена списания");
    expect(movementTypeLabel("repair_start")).toBe("Начало ремонта");
  });

  it("offers every instance state plus «all but written off» as the default", () => {
    expect(INSTANCE_STATE_OPTIONS.map((option) => option.value)).toEqual([
      "",
      "available",
      "issued",
      "in_repair",
      "written_off",
    ]);
  });
});

describe("input parsing", () => {
  it("parses rubles into whole kopecks", () => {
    expect(parseRublesToMinor("")).toBeNull();
    expect(parseRublesToMinor("1 234,50")).toBe(123450);
    expect(parseRublesToMinor("1234.5")).toBe(123450);
    expect(parseRublesToMinor("0")).toBe(0);
    expect(parseRublesToMinor("12 ₽")).toBe(1200);
    expect(parseRublesToMinor("abc")).toBeNaN();
    expect(parseRublesToMinor("1,234")).toBeNaN();
    expect(parseRublesToMinor("-5")).toBeNaN();
  });

  it("formats kopecks back into the editable rubles text", () => {
    expect(minorToRublesInput(null)).toBe("");
    expect(minorToRublesInput(123450)).toBe("1234,50");
    expect(minorToRublesInput(1205)).toBe("12,05");
    expect(minorToRublesInput(500)).toBe("5");
  });

  it("accepts only a positive whole quantity", () => {
    expect(parseQuantity("6")).toBe(6);
    expect(parseQuantity(" 10 ")).toBe(10);
    expect(parseQuantity("0")).toBeNull();
    expect(parseQuantity("1.5")).toBeNull();
    expect(parseQuantity("-1")).toBeNull();
    expect(parseQuantity("")).toBeNull();
  });
});
