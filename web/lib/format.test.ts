import { describe, expect, it } from "vitest";
import {
  addDays,
  formatByUnit,
  formatCalendarDate,
  formatCount,
  formatIST,
  formatPaise,
  formatPaiseCompact,
  formatPercent,
  formatRatio,
  paiseToRupeeInput,
  parseRupeesToPaise,
  toISTDateString,
} from "@/lib/format";
import { percentChange } from "@/lib/presentation";

const R = "₹";
const DASH = "—";

describe("formatPaise", () => {
  it("uses lakh/crore grouping", () => {
    expect(formatPaise(123456700)).toBe(`${R}12,34,567`);
    expect(formatPaise(1000000000)).toBe(`${R}1,00,00,000`);
  });
  it("handles zero, negatives and optional paise", () => {
    expect(formatPaise(0)).toBe(`${R}0`);
    expect(formatPaise(-250000)).toContain("2,500");
    expect(formatPaise(12345, { showPaise: true })).toBe(`${R}123.45`);
  });
  it("renders null/NaN as a dash, never zero", () => {
    expect(formatPaise(null)).toBe(DASH);
    expect(formatPaise(undefined)).toBe(DASH);
    expect(formatPaise(Number.NaN)).toBe(DASH);
  });
});

describe("formatPaiseCompact", () => {
  it("switches units at lakh and crore boundaries", () => {
    expect(formatPaiseCompact(99999 * 100)).toBe(`${R}99,999`);
    expect(formatPaiseCompact(100000 * 100)).toBe(`${R}1 L`);
    expect(formatPaiseCompact(450000 * 100)).toBe(`${R}4.5 L`);
    expect(formatPaiseCompact(10000000 * 100)).toBe(`${R}1 Cr`);
    expect(formatPaiseCompact(12600000 * 100)).toBe(`${R}1.26 Cr`);
  });
  it("keeps sign and null handling", () => {
    expect(formatPaiseCompact(-250000 * 100)).toBe(`-${R}2.5 L`);
    expect(formatPaiseCompact(null)).toBe(DASH);
  });
});

describe("other number formatters", () => {
  it("ratio, count, percent", () => {
    expect(formatRatio(2.345)).toBe("2.35x");
    expect(formatRatio(null)).toBe(DASH);
    expect(formatCount(1234567)).toBe("12,34,567");
    expect(formatPercent(12.34, { signed: true })).toBe("+12.3%");
    expect(formatPercent(-4.56)).toBe("-4.6%");
    expect(formatPercent(0, { signed: true })).toBe("0.0%");
    expect(formatPercent(-0.01)).toBe("0.0%");
    expect(formatPercent(null)).toBe(DASH);
  });
  it("formatByUnit dispatches on unit", () => {
    expect(formatByUnit(100000 * 100, "paise")).toBe(`${R}1 L`);
    expect(formatByUnit(3, "ratio")).toBe("3.00x");
    expect(formatByUnit(10, "count")).toBe("10");
    expect(formatByUnit(null, "paise")).toBe(DASH);
  });
});

describe("IST time formatting", () => {
  it("converts UTC to IST (+5:30)", () => {
    expect(formatIST("2026-10-14T10:00:00Z")).toBe("14 Oct 2026, 3:30 pm IST");
  });
  it("rolls the date over at the IST day boundary", () => {
    expect(formatIST("2026-10-14T19:00:00Z")).toBe("15 Oct 2026, 12:30 am IST");
    expect(toISTDateString(new Date("2026-10-14T19:00:00Z"))).toBe("2026-10-15");
    expect(toISTDateString(new Date("2026-10-14T18:29:59Z"))).toBe("2026-10-14");
  });
  it("handles missing and invalid input", () => {
    expect(formatIST(null)).toBe(DASH);
    expect(formatIST("not-a-date")).toBe(DASH);
  });
  it("calendar dates do not shift", () => {
    expect(formatCalendarDate("2026-10-01")).toBe("1 Oct 2026");
    expect(formatCalendarDate("bad")).toBe(DASH);
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });
});

describe("rupee input parsing", () => {
  it("parses valid amounts to exact paise", () => {
    expect(parseRupeesToPaise("1,234.50")).toBe(123450);
    expect(parseRupeesToPaise("0.1")).toBe(10);
    expect(parseRupeesToPaise("19.99")).toBe(1999);
    expect(parseRupeesToPaise(" 100 ")).toBe(10000);
    expect(parseRupeesToPaise(`${R}250`)).toBe(25000);
    expect(parseRupeesToPaise("0")).toBe(0);
  });
  it("rejects invalid input", () => {
    for (const bad of ["", "abc", "-5", "1.234", "1.", ".5", "1e3", "9".repeat(30)]) {
      expect(parseRupeesToPaise(bad)).toBeNull();
    }
  });
  it("round-trips", () => {
    expect(paiseToRupeeInput(123450)).toBe("1234.50");
    expect(parseRupeesToPaise(paiseToRupeeInput(5))).toBe(5);
  });
});

describe("percentChange", () => {
  it("computes change between two API values", () => {
    expect(percentChange(110, 100)).toBeCloseTo(10);
    expect(percentChange(90, 100)).toBeCloseTo(-10);
    expect(percentChange(-50, -100)).toBeCloseTo(50);
  });
  it("returns null when undefined", () => {
    expect(percentChange(10, 0)).toBeNull();
    expect(percentChange(null, 10)).toBeNull();
    expect(percentChange(10, undefined)).toBeNull();
    expect(percentChange(Number.NaN, 10)).toBeNull();
  });
});
