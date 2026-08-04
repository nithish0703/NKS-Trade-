import { describe, expect, it } from "vitest";
import {
  formatIstTime,
  formatNumberOrDash,
  formatPercentageOrDash,
  formatPriceOrDash,
  formatRelativeTime,
  formatUptime,
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

describe("formatRelativeTime", () => {
  const now = new Date("2026-01-01T10:05:00Z");

  it("returns 'never' for null", () => {
    expect(formatRelativeTime(null, now)).toBe("never");
  });

  it("returns 'never' for undefined", () => {
    expect(formatRelativeTime(undefined, now)).toBe("never");
  });

  it("returns 'never' for an unparseable value", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("never");
  });

  it("returns 'just now' for a timestamp under 5 seconds old", () => {
    expect(formatRelativeTime("2026-01-01T10:04:58Z", now)).toBe("just now");
  });

  it("formats seconds ago", () => {
    expect(formatRelativeTime("2026-01-01T10:04:30Z", now)).toBe("30s ago");
  });

  it("formats minutes ago", () => {
    expect(formatRelativeTime("2026-01-01T10:00:00Z", now)).toBe("5m ago");
  });

  it("formats hours ago", () => {
    expect(formatRelativeTime("2026-01-01T07:05:00Z", now)).toBe("3h ago");
  });

  it("formats days ago", () => {
    expect(formatRelativeTime("2025-12-30T10:05:00Z", now)).toBe("2d ago");
  });

  it("never returns a negative duration for a timestamp slightly in the future (clock skew)", () => {
    expect(formatRelativeTime("2026-01-01T10:05:02Z", now)).toBe("just now");
  });
});

describe("formatUptime", () => {
  const now = new Date("2026-01-03T12:30:45Z");

  it("returns a dash for null", () => {
    expect(formatUptime(null, now)).toBe("—");
  });

  it("returns a dash for undefined", () => {
    expect(formatUptime(undefined, now)).toBe("—");
  });

  it("returns a dash for an unparseable value", () => {
    expect(formatUptime("not-a-date", now)).toBe("—");
  });

  it("formats seconds-only uptime", () => {
    expect(formatUptime("2026-01-03T12:30:20Z", now)).toBe("25s");
  });

  it("formats minutes and seconds", () => {
    expect(formatUptime("2026-01-03T12:25:00Z", now)).toBe("5m 45s");
  });

  it("formats hours and minutes", () => {
    expect(formatUptime("2026-01-03T09:15:45Z", now)).toBe("3h 15m");
  });

  it("formats days and hours", () => {
    expect(formatUptime("2026-01-01T10:30:45Z", now)).toBe("2d 2h");
  });

  it("never returns a negative duration for a start time slightly in the future (clock skew)", () => {
    expect(formatUptime("2026-01-03T12:30:47Z", now)).toBe("0s");
  });
});

describe("formatIstTime", () => {
  it("formats an ISO UTC timestamp converted to IST (UTC+5:30)", () => {
    // 2026-01-01T10:00:00Z -> 15:30:00 IST
    expect(formatIstTime("2026-01-01T10:00:00Z")).toBe("01/01/2026, 03:30:00 pm IST");
  });

  it("returns a dash for null", () => {
    expect(formatIstTime(null)).toBe("—");
  });

  it("returns a dash for undefined", () => {
    expect(formatIstTime(undefined)).toBe("—");
  });
});
