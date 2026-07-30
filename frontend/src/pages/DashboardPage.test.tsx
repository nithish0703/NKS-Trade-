import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import { dashboardApi } from "../services/dashboard.api";
import { signalsApi } from "../services/signals.api";

vi.mock("../services/dashboard.api", () => ({
  dashboardApi: {
    getSummary: vi.fn(),
    getScanningCoins: vi.fn(),
    getActiveSignals: vi.fn(),
    getPremiumSignals: vi.fn(),
    getStrongSignals: vi.fn(),
    activateSignal: vi.fn(),
  },
}));

vi.mock("../services/signals.api", () => ({
  signalsApi: {
    getSignalDetails: vi.fn(),
  },
}));

// Prevent the real WebSocket client from attempting a connection during
// tests, while still capturing the onEvent callback so tests can
// simulate a socket message (e.g. PAIR_SCAN_UPDATED) directly.
let capturedOnEvent: ((event: unknown) => void) | null = null;
vi.mock("../hooks/useDashboardSocket", () => ({
  useDashboardSocket: (onEvent: (event: unknown) => void) => {
    capturedOnEvent = onEvent;
    return { status: "disconnected", isConnected: false };
  },
}));

const emptySummary = {
  total_signals: 0,
  wins: 0,
  losses: 0,
  open_signals: 0,
  win_rate: null,
  average_rr: null,
  premium_count: 0,
  strong_count: 0,
  scanner_running: false,
  last_scan_time_utc: null,
  server_time_utc: "2026-01-01T10:00:00Z",
  comparison: {
    total_signals_percentage: null,
    wins_percentage: null,
    losses_percentage: null,
    win_rate_percentage: null,
    average_rr_percentage: null,
  },
};

function mockAllEndpoints(overrides: Partial<Record<string, unknown>> = {}) {
  vi.mocked(dashboardApi.getSummary).mockResolvedValue(
    (overrides.summary as typeof emptySummary) ?? emptySummary,
  );
  vi.mocked(dashboardApi.getScanningCoins).mockResolvedValue((overrides.scanningCoins as never) ?? []);
  vi.mocked(dashboardApi.getActiveSignals).mockResolvedValue((overrides.activeSignals as never) ?? []);
  vi.mocked(dashboardApi.getPremiumSignals).mockResolvedValue((overrides.premiumSignals as never) ?? []);
  vi.mocked(dashboardApi.getStrongSignals).mockResolvedValue((overrides.strongSignals as never) ?? []);
  vi.mocked(dashboardApi.activateSignal).mockResolvedValue(
    (overrides.activateSignal as never) ?? {
      trade_id: "SMC-1",
      coin: "BTC-USDT",
      direction: "BUY",
      current_price: null,
      entry_price: 116980,
      take_profit: 118200,
      stop_loss: 116250,
      distance_to_take_profit_percentage: null,
      confidence_score: 96,
      signal_type: "PREMIUM",
      detection_time_utc: "2026-01-01T10:00:00Z",
      dashboard_status: "ACTIVE",
    },
  );
}

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the dashboard title and summary cards", async () => {
    mockAllEndpoints();
    render(<DashboardPage />);

    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Total Signals")).toBeInTheDocument());
  });

  it("displays '—' for a null win rate", async () => {
    mockAllEndpoints({ summary: { ...emptySummary, win_rate: null } });
    render(<DashboardPage />);

    await waitFor(() => expect(dashboardApi.getSummary).toHaveBeenCalled());
    const dashes = await screen.findAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });

  it("shows summary data once loaded", async () => {
    mockAllEndpoints({ summary: { ...emptySummary, total_signals: 324, premium_count: 5 } });
    render(<DashboardPage />);

    await waitFor(() => expect(screen.getByText("324")).toBeInTheDocument());
  });

  it("renders Premium signals returned by the API", async () => {
    mockAllEndpoints({
      premiumSignals: [
        {
          trade_id: "SMC-1",
          coin: "BTC-USDT",
          direction: "BUY",
          signal_type: "PREMIUM",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
          confidence_score: 96,
          detection_time_utc: "2026-01-01T10:00:00Z",
        },
      ],
    });
    render(<DashboardPage />);

    await waitFor(() => expect(screen.getByText("BTC-USDT")).toBeInTheDocument());
  });

  function _premiumSignalDetails(overrides: Partial<Record<string, unknown>> = {}) {
    return {
      trade_id: "SMC-1",
      coin: "BTC-USDT",
      direction: "BUY",
      signal_type: "PREMIUM",
      entry_price: 116980,
      stop_loss: 116250,
      take_profit: 118200,
      risk_reward_ratio: 2.5,
      confidence_score: 96,
      market_regime: "TRENDING",
      higher_timeframe_bias: "BULLISH",
      liquidity_type: "EQUAL_HIGH",
      entry_zone_type: "ORDER_BLOCK",
      structure_confirmation: "BOS",
      volume_confirmation: true,
      atr_status: "EXPANDING",
      trading_session: "LONDON",
      btc_market_alignment: true,
      detection_time_utc: "2026-01-01T10:00:00Z",
      institutional_reason: "Confirmed setup facts only.",
      dashboard_status: "NEW",
      ...overrides,
    };
  }

  it("opens the signal details modal when Go is clicked", async () => {
    mockAllEndpoints({
      premiumSignals: [
        {
          trade_id: "SMC-1",
          coin: "BTC-USDT",
          direction: "BUY",
          signal_type: "PREMIUM",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
          confidence_score: 96,
          detection_time_utc: "2026-01-01T10:00:00Z",
        },
      ],
    });
    vi.mocked(signalsApi.getSignalDetails).mockResolvedValue(_premiumSignalDetails() as never);

    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Go" }));

    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(await screen.findByText("Confirmed setup facts only.")).toBeInTheDocument();
  });

  it("never renders a live-trade action button when the modal is closed", async () => {
    mockAllEndpoints();
    render(<DashboardPage />);

    await waitFor(() => expect(screen.getByText("Dashboard")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /trade/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /buy now/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sell now/i })).not.toBeInTheDocument();
  });

  it("never renders buy now / sell now execution buttons, even with the modal open", async () => {
    // The modal's "Trade" button is an explicit, intentional dashboard-only
    // state transition (never a real order) -- this test confirms no
    // *execution*-style button (buy now / sell now) exists anywhere,
    // while still allowing the dashboard-only "Trade" button.
    mockAllEndpoints({
      premiumSignals: [
        {
          trade_id: "SMC-1",
          coin: "BTC-USDT",
          direction: "BUY",
          signal_type: "PREMIUM",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
          confidence_score: 96,
          detection_time_utc: "2026-01-01T10:00:00Z",
        },
      ],
    });
    vi.mocked(signalsApi.getSignalDetails).mockResolvedValue(_premiumSignalDetails() as never);

    render(<DashboardPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Go" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /buy now/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sell now/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trade" })).toBeInTheDocument();
  });

  it("moves a signal from Premium to Active when Trade is clicked", async () => {
    const premiumSignal = {
      trade_id: "SMC-1",
      coin: "BTC-USDT",
      direction: "BUY",
      signal_type: "PREMIUM",
      entry_price: 116980,
      take_profit: 118200,
      stop_loss: 116250,
      confidence_score: 96,
      detection_time_utc: "2026-01-01T10:00:00Z",
    };
    mockAllEndpoints({ premiumSignals: [premiumSignal] });
    vi.mocked(signalsApi.getSignalDetails).mockResolvedValue(_premiumSignalDetails() as never);

    render(<DashboardPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Go" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());

    // After activation, simulate the backend now excluding this signal
    // from Premium and including it in Active.
    vi.mocked(dashboardApi.getPremiumSignals).mockResolvedValue([]);
    vi.mocked(dashboardApi.getActiveSignals).mockResolvedValue([
      {
        trade_id: "SMC-1",
        coin: "BTC-USDT",
        direction: "BUY",
        current_price: null,
        entry_price: 116980,
        take_profit: 118200,
        stop_loss: 116250,
        distance_to_take_profit_percentage: null,
        confidence_score: 96,
        signal_type: "PREMIUM",
        detection_time_utc: "2026-01-01T10:00:00Z",
        dashboard_status: "ACTIVE",
      },
    ] as never);

    await user.click(screen.getByRole("button", { name: "Trade" }));

    await waitFor(() => expect(dashboardApi.activateSignal).toHaveBeenCalledWith("SMC-1"));
    // Modal closes on success.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("replaces the previous scanning-coins row when a PAIR_SCAN_UPDATED event arrives", async () => {
    mockAllEndpoints({
      scanningCoins: [
        {
          coin: "BTC-USDT",
          direction: null,
          score: null,
          status: "ERROR",
          failed_layer: null,
          reason: "Required market data is unavailable.",
          updated_at_utc: "2026-01-01T10:00:00Z",
          validation_progress_raw_score: null,
          validation_progress_max_score: null,
          validation_progress_percentage: null,
          last_executed_layer: null,
          preview_direction: null,
          preview_progress_raw_score: null,
          preview_progress_max_score: null,
          preview_progress_percentage: null,
          preview_completed_layers: null,
          preview_failed_layers: null,
          preview_data_availability: null,
        },
      ],
    });
    render(<DashboardPage />);

    await waitFor(() => expect(screen.getByText("BTC-USDT")).toBeInTheDocument());
    // Old ERROR-cycle data is visible: no percentage rendered.
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument();

    // A newer cycle result resolved BTC-USDT to a REJECTED status with a
    // partial preview progress; simulate the next REST refresh returning
    // this newer row (as would happen after PAIR_SCAN_UPDATED triggers a
    // scanningCoins.refresh()).
    vi.mocked(dashboardApi.getScanningCoins).mockResolvedValue([
      {
        coin: "BTC-USDT",
        direction: null,
        score: null,
        status: "REJECTED",
        failed_layer: "MARKET_REGIME",
        reason: "ATR expansion ratio is insufficient.",
        updated_at_utc: "2026-01-01T10:00:15Z",
        validation_progress_raw_score: 0,
        validation_progress_max_score: 120,
        validation_progress_percentage: 0,
        last_executed_layer: "MARKET_REGIME",
        preview_direction: "BUY",
        preview_progress_raw_score: 40,
        preview_progress_max_score: 120,
        preview_progress_percentage: 33,
        preview_completed_layers: ["MARKET_REGIME", "HTF_BIAS"],
        preview_failed_layers: ["LIQUIDITY_SWEEP"],
        preview_data_availability: { LIQUIDITY_SWEEP: "FAILED" },
      },
    ]);

    expect(capturedOnEvent).not.toBeNull();
    act(() => {
      capturedOnEvent?.({
        event: "PAIR_SCAN_UPDATED",
        timestamp_utc: "2026-01-01T10:00:15Z",
        data: { coin: "BTC-USDT" },
      });
    });

    // The old ERROR row is gone; only the newest data for BTC-USDT remains
    // (a single row, keyed by coin, never duplicated).
    await waitFor(() => expect(screen.getByText("33%")).toBeInTheDocument());
    expect(screen.getAllByText("BTC-USDT")).toHaveLength(1);
  });
});
