import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ScanningCoinsTable, scanningCoinsSummary } from "./ScanningCoinsTable";
import type { ScanningCoin } from "../types/dashboard";

function coin(overrides: Partial<ScanningCoin> = {}): ScanningCoin {
  return {
    coin: "BTC-USDT",
    price: 65432.1,
    direction: "BUY",
    score: 6,
    status: "READY",
    failed_layer: null,
    reason: null,
    updated_at_utc: "2026-01-01T10:00:00Z",
    validation_progress_raw_score: 6,
    validation_progress_max_score: 6,
    validation_progress_percentage: 100,
    last_executed_layer: "RISK_MANAGEMENT",
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

  it("shows a muted double-dash inside the circle for a missing score", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "SCANNING",
            validation_progress_percentage: null,
            validation_progress_raw_score: null,
            validation_progress_max_score: null,
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

  it("renders the Coin, Live Price, Direction, Score (%), and Status headings only", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.getByText("Coin")).toBeInTheDocument();
    expect(screen.getByText("Live Price")).toBeInTheDocument();
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
    expect(screen.getByText("1H trend is BEARISH; no BUY permitted.")).toBeInTheDocument();
  });

  it("truncates a very long inline reason but keeps the full text in the tooltip", () => {
    const longReason =
      "Correlation with an active position is too high across the correlation " +
      "window and the candidate symbol was therefore rejected by risk management.";
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "REJECTED",
            score: null,
            direction: null,
            failed_layer: "RISK_MANAGEMENT",
            reason: longReason,
          }),
        ]}
      />,
    );
    expect(screen.getByText("REJECTED: RISK_MANAGEMENT")).toBeInTheDocument();
    expect(screen.queryByText(longReason)).not.toBeInTheDocument();
    const truncated = screen.getByText(/^Correlation with an active position is too high/);
    expect(truncated.textContent?.endsWith("…")).toBe(true);
    expect(truncated.textContent?.length).toBeLessThan(longReason.length);
    const statusCell = truncated.closest("td");
    expect(statusCell?.getAttribute("title")).toContain(longReason);
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
            reason: "Pipeline result is not CONFIRMED.",
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
          coin({ coin: "REJECTED-USDT", status: "REJECTED", failed_layer: "HTF_BIAS" }),
        ]}
      />,
    );
    const readyStatus = screen.getByText("READY");
    const rejectedStatus = screen.getByText("REJECTED: HTF_BIAS");
    expect(readyStatus.className).not.toBe(rejectedStatus.className);
  });

  it("renders BUY direction in a green, semibold style", () => {
    render(<ScanningCoinsTable coins={[coin({ direction: "BUY" })]} />);
    const buyText = screen.getByText("BUY");
    expect(buyText.className).toContain("text-emerald-600");
    expect(buyText.className).toContain("font-semibold");
  });

  it("renders SELL direction in a red, semibold style", () => {
    render(<ScanningCoinsTable coins={[coin({ direction: "SELL" })]} />);
    const sellText = screen.getByText("SELL");
    expect(sellText.className).toContain("text-red-600");
    expect(sellText.className).toContain("font-semibold");
  });

  it("renders a muted dash for an unknown/null direction", () => {
    render(<ScanningCoinsTable coins={[coin({ direction: null })]} />);
    const dash = screen.getByText("—");
    expect(dash.className).toContain("text-slate-400");
  });

  it("displays the validation progress percentage as text", () => {
    render(<ScanningCoinsTable coins={[coin({ validation_progress_percentage: 72 })]} />);
    expect(screen.getByText("72%")).toBeInTheDocument();
  });

  it("renders a circular progress indicator matching the progress percentage", () => {
    render(<ScanningCoinsTable coins={[coin({ validation_progress_percentage: 63 })]} />);
    const svgs = document.querySelectorAll("table svg");
    expect(svgs.length).toBeGreaterThan(0);
    const progressCircle = svgs[0]?.querySelectorAll("circle")[1];
    expect(progressCircle).toBeDefined();
    expect(progressCircle?.getAttribute("stroke-dashoffset")).not.toBeNull();
  });

  it("shows a partial progress score even when the real pipeline is REJECTED", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            status: "REJECTED",
            score: 2,
            direction: "BUY",
            failed_layer: "BOS",
            reason: "No confirmed structure break.",
            validation_progress_raw_score: 2,
            validation_progress_percentage: 33,
            last_executed_layer: "BOS",
          }),
        ]}
      />,
    );
    expect(screen.getByText("33%")).toBeInTheDocument();
    expect(screen.getByText("BUY")).toBeInTheDocument();
  });

  it("never labels the progress percentage as confidence", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
  });

  it("never renders a hardcoded percentage when progress is unavailable", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({
            validation_progress_percentage: null,
            validation_progress_raw_score: null,
            validation_progress_max_score: null,
          }),
        ]}
      />,
    );
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("orders coins by live score, highest first", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "SOL-USDT", score: 1 }),
          coin({ coin: "BTC-USDT", score: 5 }),
          coin({ coin: "ETH-USDT", score: 3 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1); // skip header row
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("BTC-USDT");
    expect(coinNames[1]).toContain("ETH-USDT");
    expect(coinNames[2]).toContain("SOL-USDT");
  });

  it("breaks tied scores alphabetically for a deterministic order", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "SOL-USDT", score: 3 }),
          coin({ coin: "BTC-USDT", score: 3 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("BTC-USDT");
    expect(coinNames[1]).toContain("SOL-USDT");
  });

  it("sinks coins with no score yet to the bottom instead of the top", () => {
    render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "NEW-USDT", status: "SCANNING", score: null }),
          coin({ coin: "BTC-USDT", score: 1 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("BTC-USDT");
    expect(coinNames[1]).toContain("NEW-USDT");
  });

  it("does not reshuffle row order between renders that happen inside the same stabilization window", () => {
    // Regression test for a screen-blinking bug: resorting on every
    // render caused every row to reshuffle position on each
    // WebSocket-driven score update (which can fire many times per
    // scan cycle), making the whole table appear to flicker. The
    // ranking is only recomputed on a timer (see
    // SORT_STABILIZATION_INTERVAL_MS in the component), so scores can
    // update in place many times without moving any row.
    const { rerender } = render(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "AAA-USDT", score: 1 }),
          coin({ coin: "BBB-USDT", score: 2 }),
        ]}
      />,
    );
    let rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)[0]).toContain("BBB-USDT");

    // AAA-USDT's score jumps far above BBB-USDT's, but no time has
    // passed -- the row order must not change mid-window.
    rerender(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "AAA-USDT", score: 6 }),
          coin({ coin: "BBB-USDT", score: 2 }),
        ]}
      />,
    );
    rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)[0]).toContain("BBB-USDT");
  });

  it("re-ranks rows once the stabilization interval elapses", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <ScanningCoinsTable
          coins={[
            coin({ coin: "AAA-USDT", score: 1 }),
            coin({ coin: "BBB-USDT", score: 2 }),
          ]}
        />,
      );
      let rows = screen.getAllByRole("row").slice(1);
      expect(rows.map((row) => row.textContent)[0]).toContain("BBB-USDT");

      rerender(
        <ScanningCoinsTable
          coins={[
            coin({ coin: "AAA-USDT", score: 6 }),
            coin({ coin: "BBB-USDT", score: 2 }),
          ]}
        />,
      );
      act(() => {
        vi.advanceTimersByTime(5000);
      });
      rows = screen.getAllByRole("row").slice(1);
      expect(rows.map((row) => row.textContent)[0]).toContain("AAA-USDT");
    } finally {
      vi.useRealTimers();
    }
  });

  it("appends a newly discovered coin at the end instead of forcing an immediate resort", () => {
    const { rerender } = render(
      <ScanningCoinsTable coins={[coin({ coin: "BTC-USDT", score: 3 })]} />,
    );
    rerender(
      <ScanningCoinsTable
        coins={[
          coin({ coin: "BTC-USDT", score: 3 }),
          coin({ coin: "ETH-USDT", score: 6 }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    const coinNames = rows.map((row) => row.textContent);
    expect(coinNames[0]).toContain("BTC-USDT");
    expect(coinNames[1]).toContain("ETH-USDT");
  });

  it("shows the ranking-only tooltip notice on the score cell", () => {
    render(<ScanningCoinsTable coins={[coin()]} />);
    const scoreCell = screen.getByText("100%").closest("[title]");
    expect(scoreCell?.getAttribute("title")).toContain(
      "Ranking only. This is not a trading signal or confidence score.",
    );
  });

  describe("live price column", () => {
    it("renders the formatted live price for a coin", () => {
      render(<ScanningCoinsTable coins={[coin({ price: 65432.1 })]} />);
      expect(screen.getByText("65,432.1")).toBeInTheDocument();
    });

    it("shows a dash when the live price is unavailable", () => {
      render(<ScanningCoinsTable coins={[coin({ price: null })]} />);
      expect(screen.getByText("—")).toBeInTheDocument();
    });

    it("renders a different price per row, independent of scan status", () => {
      render(
        <ScanningCoinsTable
          coins={[
            coin({ coin: "BTC-USDT", price: 65000, status: "READY" }),
            coin({ coin: "ETH-USDT", price: 3200, status: "SCANNING" }),
          ]}
        />,
      );
      expect(screen.getByText("65,000")).toBeInTheDocument();
      expect(screen.getByText("3,200")).toBeInTheDocument();
    });
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

  describe("counts", () => {
    it("shows the total coin count", () => {
      render(
        <ScanningCoinsTable
          coins={[coin({ coin: "BTC-USDT" }), coin({ coin: "ETH-USDT" }), coin({ coin: "SOL-USDT" })]}
        />,
      );
      expect(screen.getByText("3")).toBeInTheDocument();
      expect(screen.getByText(/total coins/)).toBeInTheDocument();
    });

    it("uses singular wording for exactly one coin", () => {
      render(<ScanningCoinsTable coins={[coin()]} />);
      expect(screen.getByText(/total coin$/)).toBeInTheDocument();
    });

    it("shows the scanning-in-progress count when at least one coin is still SCANNING", () => {
      render(
        <ScanningCoinsTable
          coins={[
            coin({ coin: "BTC-USDT", status: "READY" }),
            coin({ coin: "ETH-USDT", status: "SCANNING" }),
            coin({ coin: "SOL-USDT", status: "SCANNING" }),
          ]}
        />,
      );
      expect(screen.getByText("2")).toBeInTheDocument();
      expect(screen.getByText(/scanning/)).toBeInTheDocument();
    });

    it("hides the scanning count when every coin has a result", () => {
      render(
        <ScanningCoinsTable
          coins={[
            coin({ coin: "BTC-USDT", status: "READY" }),
            coin({ coin: "ETH-USDT", status: "REJECTED" }),
          ]}
        />,
      );
      expect(screen.queryByText(/scanning$/)).not.toBeInTheDocument();
    });

    it("the total count is unaffected by the search filter", () => {
      render(
        <ScanningCoinsTable coins={[coin({ coin: "BTC-USDT" }), coin({ coin: "ETH-USDT" })]} />,
      );
      fireEvent.change(screen.getByPlaceholderText("Search coin..."), {
        target: { value: "btc" },
      });
      expect(screen.getByText("2")).toBeInTheDocument();
    });
  });
});

describe("scanningCoinsSummary", () => {
  it("counts total and scanning-status coins", () => {
    const summary = scanningCoinsSummary([
      coin({ coin: "A", status: "READY" }),
      coin({ coin: "B", status: "SCANNING" }),
      coin({ coin: "C", status: "SCANNING" }),
      coin({ coin: "D", status: "REJECTED" }),
    ]);
    expect(summary).toEqual({ total: 4, scanning: 2 });
  });

  it("returns zero counts for an empty list", () => {
    expect(scanningCoinsSummary([])).toEqual({ total: 0, scanning: 0 });
  });
});
