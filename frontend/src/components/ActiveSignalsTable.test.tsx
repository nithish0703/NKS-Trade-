import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ActiveSignalsTable } from "./ActiveSignalsTable";
import type { ActiveSignal } from "../types/dashboard";

function signal(overrides: Partial<ActiveSignal> = {}): ActiveSignal {
  return {
    trade_id: "SMC-1",
    coin: "BTC-USDT",
    direction: "BUY",
    current_price: 117450,
    entry_price: 116980,
    take_profit: 118200,
    stop_loss: 116250,
    distance_to_take_profit_percentage: 0.64,
    confidence_score: 96,
    signal_type: "PREMIUM",
    detection_time_utc: "2026-01-01T10:00:00Z",
    dashboard_status: "ACTIVE",
    ...overrides,
  };
}

describe("ActiveSignalsTable", () => {
  it("renders signal rows", () => {
    render(<ActiveSignalsTable signals={[signal()]} />);
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    expect(screen.getByText("117,450")).toBeInTheDocument();
  });

  it("shows ACTIVE status for each signal", () => {
    render(<ActiveSignalsTable signals={[signal()]} />);
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("shows an empty state when there are no active signals", () => {
    render(<ActiveSignalsTable signals={[]} />);
    expect(screen.getByText("No active signals")).toBeInTheDocument();
  });

  it("shows a dash when current price is unavailable", () => {
    render(<ActiveSignalsTable signals={[signal({ current_price: null, distance_to_take_profit_percentage: null })]} />);
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });
});
