import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ScanningCoinsTable } from "./ScanningCoinsTable";
import type { ScanningCoin } from "../types/dashboard";

function coin(overrides: Partial<ScanningCoin> = {}): ScanningCoin {
  return {
    coin: "BTC-USDT",
    direction: "BUY",
    score: 96,
    status: "READY",
    failed_layer: null,
    reason: null,
    updated_at_utc: "2026-01-01T10:00:00Z",
    validation_progress_raw_score: 120,
    validation_progress_max_score: 120,
    validation_progress_percentage: 100,
    last_executed_layer: "CONFIDENCE_SCORING",
    preview_direction: "BUY",
    preview_progress_raw_score: 120,
    preview_progress_max_score: 120,
    preview_progress_percentage: 100,
    preview_completed_layers: ["MARKET_REGIME", "HTF_BIAS"],
    preview_failed_layers: [],
    preview_data_availability: { MARKET_REGIME: "PASSED", HTF_BIAS: "PASSED" },
    chart_trend: "UP",
    ...overrides,
  };
}

describe("ScanningCoinsTable", () => {
  it("renders a row per coin", () => {
    render(<ScanningCoinsTable coins={[coin(), coin({ coin: "ETH-USDT" })]} />);
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    expect(screen.getByText("ETH-USDT")).toBeInTheDocument();
  });

  it("shows an empty state when there are no coins", () => {
    render(<ScanningCoinsTable coins={[]} />);
    expect(screen.getByText("Waiting for scanner")).toBeInTheDocument();
  });

  it("shows a dash for a missing preview score", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "SCANNING",
            preview_progress_percentage: null,
            preview_progress_raw_score: null,
            preview_progress_max_score: null,
          }),
        ]}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("does not render a Status column heading", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.queryByText("Status")).not.toBeInTheDocument();
  });

  it("renders the Coin, Direction, Chart, and Score (%) headings only", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.getByText("Coin")).toBeInTheDocument();
    expect(screen.getByText("Direction")).toBeInTheDocument();
    expect(screen.getByText("Chart")).toBeInTheDocument();
    expect(screen.getByText("Score (%)")).toBeInTheDocument();
  });

  it("renders BUY preview direction in a green, semibold style", () => {
    render(<ScanningCoinsTable coins={[coin({ preview_direction: "BUY" })]} />);
    const buyText = screen.getByText("BUY");
    expect(buyText.className).toContain("text-emerald-600");
    expect(buyText.className).toContain("font-semibold");
  });

  it("renders SELL preview direction in a red, semibold style", () => {
    render(<ScanningCoinsTable coins={[coin({ preview_direction: "SELL" })]} />);
    const sellText = screen.getByText("SELL");
    expect(sellText.className).toContain("text-red-600");
    expect(sellText.className).toContain("font-semibold");
  });

  it("renders a muted dash for an unknown/null preview direction", () => {
    render(<ScanningCoinsTable coins={[coin({ preview_direction: null })]} />);
    const dash = screen.getByText("—");
    expect(dash.className).toContain("text-slate-400");
  });

  it("displays the preview progress percentage as text", () => {
    render(<ScanningCoinsTable coins={[coin({ preview_progress_percentage: 72 })]} />);
    expect(screen.getByText("72%")).toBeInTheDocument();
  });

  it("renders a circular progress indicator matching the preview percentage", () => {
    render(<ScanningCoinsTable coins={[coin({ preview_progress_percentage: 63 })]} />);
    const svg = document.querySelector("svg");
    expect(svg).not.toBeNull();
    const progressCircle = svg?.querySelectorAll("circle")[1];
    expect(progressCircle).toBeDefined();
    expect(progressCircle?.getAttribute("stroke-dashoffset")).not.toBeNull();
  });

  it("shows a partial preview score even when the real pipeline is REJECTED", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "REJECTED",
            score: null,
            direction: null,
            failed_layer: "MARKET_REGIME",
            reason: "ATR expansion ratio is insufficient.",
            validation_progress_raw_score: 0,
            validation_progress_percentage: 0,
            last_executed_layer: "MARKET_REGIME",
            preview_direction: "BUY",
            preview_progress_percentage: 33,
            preview_completed_layers: ["MARKET_REGIME", "HTF_BIAS"],
          }),
        ]}
      />,
    );
    expect(screen.getByText("33%")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("never labels the preview percentage as confidence", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  });

  it("never renders a hardcoded percentage when the preview is unavailable", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            preview_progress_percentage: null,
            preview_progress_raw_score: null,
            preview_progress_max_score: null,
          }),
        ]}
      />,
    );
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("shows the dashboard-preview-only tooltip notice on the score cell", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    const scoreCell = screen.getByText("100%").closest("div");
    expect(scoreCell?.getAttribute("title")).toContain(
      "Dashboard preview only. This is not final trade confidence.",
    );
  });

  it("renders an up arrow in green for an UP chart trend", () => {
    render(<ScanningCoinsTable coins={[coin({ chart_trend: "UP" })]} />);
    const arrow = screen.getByText("↑");
    expect(arrow.className).toContain("text-emerald-600");
  });

  it("renders a down arrow in red for a DOWN chart trend", () => {
    render(<ScanningCoinsTable coins={[coin({ chart_trend: "DOWN" })]} />);
    const arrow = screen.getByText("↓");
    expect(arrow.className).toContain("text-red-600");
  });

  it("renders a dash when chart trend is unavailable", () => {
    render(<ScanningCoinsTable coins={[coin({ chart_trend: null })]} />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("chart trend is independent of preview_direction", () => {
    // Even when preview_direction is null (e.g. HTF bias MIXED), the raw
    // chart trend can still be shown since it's a different signal.
    render(<ScanningCoinsTable coins={[coin({ preview_direction: null, chart_trend: "UP" })]} />);
    expect(screen.getByText("↑")).toBeInTheDocument();
  });
});
