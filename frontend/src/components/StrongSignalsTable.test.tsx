import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StrongSignalsTable } from "./StrongSignalsTable";
import type { StrongSignal } from "../types/dashboard";

function signal(overrides: Partial<StrongSignal> = {}): StrongSignal {
  return {
    trade_id: "SMC-2",
    coin: "ETH-USDT",
    direction: "SELL",
    confidence_score: 85,
    higher_timeframe_bias: "BEARISH",
    normalized_score: 85,
    detection_time_utc: "2026-01-01T10:00:00Z",
    ...overrides,
  };
}

describe("StrongSignalsTable", () => {
  it("renders strong signal rows with BUY or SELL direction", () => {
    render(
      <StrongSignalsTable
        signals={[signal(), signal({ trade_id: "SMC-3", coin: "SOL-USDT", direction: "BUY" })]}
      />,
    );
    expect(screen.getByText("ETH-USDT")).toBeInTheDocument();
    expect(screen.getByText("SOL-USDT")).toBeInTheDocument();
    expect(screen.getByText("SELL")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("shows an empty state when there are no strong signals", () => {
    render(<StrongSignalsTable signals={[]} />);
    expect(screen.getByText("No Strong signals yet")).toBeInTheDocument();
  });

  it("never renders a MEDIUM classification label", () => {
    render(<StrongSignalsTable signals={[signal()]} />);
    expect(screen.queryByText("MEDIUM")).not.toBeInTheDocument();
  });
});
