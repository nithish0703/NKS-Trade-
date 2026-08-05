import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PremiumSignalsTable } from "./PremiumSignalsTable";
import type { PremiumSignal } from "../types/dashboard";

function signal(overrides: Partial<PremiumSignal> = {}): PremiumSignal {
  return {
    trade_id: "SMC-1",
    coin: "BTC-USDT",
    direction: "BUY",
    status: "CONFIRMED",
    entry_price: 116980,
    take_profit: 118200,
    stop_loss: 116250,
    detection_time_utc: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

describe("PremiumSignalsTable", () => {
  it("renders a Go button, not a View or Trade button", () => {
    render(<PremiumSignalsTable signals={[signal()]} onView={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "View" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /trade/i })).not.toBeInTheDocument();
  });

  it("calls onView with the trade_id when Go is clicked", () => {
    const onView = vi.fn();
    render(<PremiumSignalsTable signals={[signal({ trade_id: "SMC-42" })]} onView={onView} />);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onView).toHaveBeenCalledWith("SMC-42");
  });

  it("shows an empty state when there are no premium signals", () => {
    render(<PremiumSignalsTable signals={[]} onView={vi.fn()} />);
    expect(screen.getByText("No Premium signals yet")).toBeInTheDocument();
  });

  it("never renders a MEDIUM tier label", () => {
    render(<PremiumSignalsTable signals={[signal()]} onView={vi.fn()} />);
    expect(screen.queryByText("MEDIUM")).not.toBeInTheDocument();
  });

  it("shows a Premium only placeholder when status is null", () => {
    render(<PremiumSignalsTable signals={[signal({ status: null })]} onView={vi.fn()} />);
    expect(screen.getByText("Premium only")).toBeInTheDocument();
  });

  it("shows a CONFIRMED status badge", () => {
    render(<PremiumSignalsTable signals={[signal({ status: "CONFIRMED" })]} onView={vi.fn()} />);
    expect(screen.getByText("CONFIRMED")).toBeInTheDocument();
  });

  it("shows the signal detection time in IST", () => {
    render(
      <PremiumSignalsTable
        signals={[signal({ detection_time_utc: "2026-01-01T10:00:00Z" })]}
        onView={vi.fn()}
      />,
    );
    expect(screen.getByText(/Signal given:/)).toBeInTheDocument();
    expect(screen.getByText(/IST/)).toBeInTheDocument();
  });
});
