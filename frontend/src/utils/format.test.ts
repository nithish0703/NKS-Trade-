import { describe, expect, it } from "vitest";
import {
  formatNumberOrDash,
  formatPercentageOrDash,
  formatPriceOrDash,
  formatUtcTime,
} from "./format";

describe("formatNumberOrDash", () => {
  it("formats a number", () => {
    expect(formatNumberOrDash(324)).toBe("324");
  });

  it("returns a dash for null", () => {
    expect(formatNumberOrDash(null)).toBe("—");
  });

  it("returns a dash for undefined", () => {
    expect(formatNumberOrDash(undefined)).toBe("—");
  });

  it("returns a dash for NaN", () => {
    expect(formatNumberOrDash(Number.NaN)).toBe("—");
  });
});

describe("formatPercentageOrDash", () => {
  it("formats a percentage with one decimal by default", () => {
    expect(formatPercentageOrDash(61.234)).toBe("61.2%");
  });

  it("returns a dash for null (e.g. win rate unavailable)", () => {
    expect(formatPercentageOrDash(null)).toBe("—");
  });
});

describe("formatPriceOrDash", () => {
  it("formats a price", () => {
    expect(formatPriceOrDash(117450)).toBe("117,450");
  });

  it("returns a dash for null (e.g. current price unavailable)", () => {
    expect(formatPriceOrDash(null)).toBe("—");
  });
});

describe("formatUtcTime", () => {
  it("formats an ISO timestamp with a UTC suffix", () => {
    expect(formatUtcTime("2026-01-01T10:00:00Z")).toBe("2026-01-01 10:00:00 UTC");
  });

  it("returns a dash for null", () => {
    expect(formatUtcTime(null)).toBe("—");
  });

  it("returns a dash for undefined", () => {
    expect(formatUtcTime(undefined)).toBe("—");
  });
});
