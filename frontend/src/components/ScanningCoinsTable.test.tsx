import { fireEvent, render, screen } from "@testing-library/react";
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

  it("shows a muted double-dash inside the circle for a missing preview score", () => {
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
    const dash = screen.getByText("--");
    expect(dash.className).toContain("text-slate-400");
  });

  it("renders a Status column heading", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.getByText("Status")).toBeInTheDocument();
  });

  it("renders the Coin, Direction, Score (%), and Status headings only", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.getByText("Coin")).toBeInTheDocument();
    expect(screen.getByText("Direction")).toBeInTheDocument();
    expect(screen.getByText("Score (%)")).toBeInTheDocument();
    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.queryByText("Chart")).not.toBeInTheDocument();
  });

  it("shows the rejection reason inline for a REJECTED coin instead of only on hover", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "REJECTED",
            score: null,
            direction: null,
            failed_layer: "HTF_BIAS",
            reason: "1H trend is BEARISH; no BUY permitted.",
          }),
        ]}
      />,
    );
    expect(screen.getByText("REJECTED: HTF_BIAS")).toBeInTheDocument();
  });

  it("shows a plain REJECTED label when no failed_layer is available", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "REJECTED",
            score: null,
            direction: null,
            failed_layer: null,
            reason: "Pipeline result is not a publishable PREMIUM/STRONG setup.",
          }),
        ]}
      />,
    );
    expect(screen.getByText("REJECTED")).toBeInTheDocument();
  });

  it("shows SCANNING status for a coin with no result yet", () => {
    render(<ScanningCoinsTable coins={[coin({ status: "SCANNING" })]} />);
    expect(screen.getByText("SCANNING")).toBeInTheDocument();
  });

  it("summarizes an insufficient-candle-history ERROR instead of the full backend sentence", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "ERROR",
            score: null,
            direction: null,
            reason:
              "Required market data or indicator calculation is unavailable. " +
              "(Failed to calculate indicators for 1 timeframe(s): 4h: " +
              "Insufficient candles to calculate a 200-period EMA200.)",
          }),
        ]}
      />,
    );
    expect(screen.getByText("ERROR: not enough history for EMA200 (needs 200 candles)")).toBeInTheDocument();
  });

  it("shows a plain ERROR label when no reason is available", () => {
    render(
      <ScanningCoinsTable
        coins={[coin({ status: "ERROR", score: null, direction: null, reason: null })]}
      />,
    );
    expect(screen.getByText("ERROR")).toBeInTheDocument();
  });

  it("shows the full untruncated reason for a non-candle-history ERROR", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "ERROR",
            score: null,
            direction: null,
            reason: "Unexpected technical error during pair scan.",
          }),
        ]}
      />,
    );
    expect(
      screen.getByText("ERROR: Unexpected technical error during pair scan."),
    ).toBeInTheDocument();
  });

  it("shows READY status in a distinct color from REJECTED", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "READY-USDT", status: "READY" }),
          coin({ coin: "REJECTED-USDT", status: "REJECTED", failed_layer: "MARKET_REGIME" }),
        ]}
      />,
    );
    const readyStatus = screen.getByText("READY");
    const rejectedStatus = screen.getByText("REJECTED: MARKET_REGIME");
    expect(readyStatus.className).not.toBe(rejectedStatus.className);
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
    const svgs = document.querySelectorAll("table svg");
    expect(svgs.length).toBeGreaterThan(0);
    const progressCircle = svgs[0]?.querySelectorAll("circle")[1];
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

  it("orders coins by score descending, highest first", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "LOW-USDT", preview_progress_percentage: 10 }),
          coin({ coin: "HIGH-USDT", preview_progress_percentage: 90 }),
          coin({ coin: "MID-USDT", preview_progress_percentage: 50 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1); // skip header row
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("HIGH-USDT");
    expect(coinNames[1]).toContain("MID-USDT");
    expect(coinNames[2]).toContain("LOW-USDT");
  });

  it("places coins with a null score last", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "SCORED-USDT", preview_progress_percentage: 10 }),
          coin({
            coin: "SCANNING-USDT",
            preview_progress_percentage: null,
            preview_progress_raw_score: null,
            preview_progress_max_score: null,
          }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("SCORED-USDT");
    expect(coinNames[1]).toContain("SCANNING-USDT");
  });

  it("shows the dashboard-preview-only tooltip notice on the score cell", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    const scoreCell = screen.getByText("100%").closest("[title]");
    expect(scoreCell?.getAttribute("title")).toContain(
      "Dashboard preview only. This is not final trade confidence.",
    );
  });

  describe("search", () => {
    it("renders a search input", () => {
      render(<ScanningCoinsTable coins={[coin()]} />);
      expect(screen.getByPlaceholderText("Search coin...")).toBeInTheDocument();
    });

    it("filters coins by name as the user types", () => {
      render(
        <ScanningCoinsTable
          coins={[coin({ coin: "BTC-USDT" }), coin({ coin: "ETH-USDT" }), coin({ coin: "SOL-USDT" })]}
        />,
      );
      fireEvent.change(screen.getByPlaceholderText("Search coin..."), {
        target: { value: "eth" },
      });
      expect(screen.getByText("ETH-USDT")).toBeInTheDocument();
      expect(screen.queryByText("BTC-USDT")).not.toBeInTheDocument();
      expect(screen.queryByText("SOL-USDT")).not.toBeInTheDocument();
    });

    it("search is case-insensitive", () => {
      render(<ScanningCoinsTable coins={[coin({ coin: "BTC-USDT" })]} />);
      fireEvent.change(screen.getByPlaceholderText("Search coin..."), {
        target: { value: "btc" },
      });
      expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    });

    it("shows an empty state when no coin matches the search", () => {
      render(<ScanningCoinsTable coins={[coin({ coin: "BTC-USDT" })]} />);
      fireEvent.change(screen.getByPlaceholderText("Search coin..."), {
        target: { value: "doesnotexist" },
      });
      expect(screen.getByText("No coins match your search")).toBeInTheDocument();
    });

    it("clearing the search restores the full list", () => {
      render(<ScanningCoinsTable coins={[coin({ coin: "BTC-USDT" }), coin({ coin: "ETH-USDT" })]} />);
      const input = screen.getByPlaceholderText("Search coin...");
      fireEvent.change(input, { target: { value: "btc" } });
      expect(screen.queryByText("ETH-USDT")).not.toBeInTheDocument();
      fireEvent.change(input, { target: { value: "" } });
      expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
      expect(screen.getByText("ETH-USDT")).toBeInTheDocument();
    });
  });
});
