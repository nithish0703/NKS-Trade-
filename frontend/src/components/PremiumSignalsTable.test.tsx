import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PremiumSignalsTable } from "./PremiumSignalsTable";
import type { PremiumSignal } from "../types/dashboard";

function signal(overrides: Partial<PremiumSignal> = {}): PremiumSignal {
  return {
    trade_id: "SMC-1",
    coin: "BTC-USDT",
    direction: "BUY",
    signal_type: "PREMIUM",
    entry_price: 116980,
    take_profit: 118200,
    stop_loss: 116250,
    confidence_score: 96,
    detection_time_utc: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

describe("PremiumSignalsTable", () => {
  it("renders a View button, not a Trade button", () => {
    render(<PremiumSignalsTable signals={[signal()]} onView={vi.fn()} />);
    expect(screen.getByRole("button", { name: "View" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /trade/i })).not.toBeInTheDocument();
  });

  it("calls onView with the trade_id when clicked", () => {
    const onView = vi.fn();
    render(<PremiumSignalsTable signals={[signal({ trade_id: "SMC-42" })]} onView={onView} />);
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    expect(onView).toHaveBeenCalledWith("SMC-42");
  });

  it("shows an empty state when there are no premium signals", () => {
    render(<PremiumSignalsTable signals={[]} onView={vi.fn()} />);
    expect(screen.getByText("No Premium signals yet")).toBeInTheDocument();
  });

  it("never renders a signal_type other than PREMIUM as the tier label", () => {
    render(<PremiumSignalsTable signals={[signal()]} onView={vi.fn()} />);
    expect(screen.getByText("PREMIUM")).toBeInTheDocument();
    expect(screen.queryByText("MEDIUM")).not.toBeInTheDocument();
  });
});
