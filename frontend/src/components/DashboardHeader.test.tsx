import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardHeader } from "./DashboardHeader";

describe("DashboardHeader", () => {
  it("shows 'never' when no scan has completed yet", () => {
    render(<DashboardHeader isConnected lastScanTimeUtc={null} />);
    expect(screen.getByText("Last scan: never")).toBeInTheDocument();
  });

  it("shows 'never' when lastScanTimeUtc is not provided", () => {
    render(<DashboardHeader isConnected />);
    expect(screen.getByText("Last scan: never")).toBeInTheDocument();
  });

  it("shows a relative time for a recent scan", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T10:00:30Z"));
    render(<DashboardHeader isConnected lastScanTimeUtc="2026-01-01T10:00:00Z" />);
    expect(screen.getByText("Last scan: 30s ago")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows the exact UTC timestamp in the title attribute for hover", () => {
    render(<DashboardHeader isConnected lastScanTimeUtc="2026-01-01T10:00:00Z" />);
    const badge = screen.getByText(/Last scan:/);
    expect(badge.getAttribute("title")).toBe("2026-01-01 10:00:00 UTC");
  });

  it("still renders the connection status and clocks", () => {
    render(<DashboardHeader isConnected lastScanTimeUtc={null} />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/UTC$/)).toBeInTheDocument();
    expect(screen.getByText(/IST$/)).toBeInTheDocument();
  });

  it("shows a dash for uptime when serverStartedAtUtc is not provided", () => {
    render(<DashboardHeader isConnected serverStartedAtUtc={null} />);
    expect(screen.getByText("Uptime: —")).toBeInTheDocument();
  });

  it("shows the formatted uptime duration", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T12:00:00Z"));
    render(<DashboardHeader isConnected serverStartedAtUtc="2026-01-01T09:00:00Z" />);
    expect(screen.getByText("Uptime: 3h 0m")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows the exact server start time in the title attribute for hover", () => {
    render(<DashboardHeader isConnected serverStartedAtUtc="2026-01-01T09:00:00Z" />);
    const badge = screen.getByText(/Uptime:/);
    expect(badge.getAttribute("title")).toBe("API started at 2026-01-01 09:00:00 UTC");
  });

  it("shows an unavailable message in the title when serverStartedAtUtc is missing", () => {
    render(<DashboardHeader isConnected serverStartedAtUtc={null} />);
    const badge = screen.getByText(/Uptime:/);
    expect(badge.getAttribute("title")).toBe("API start time unavailable.");
  });
});
