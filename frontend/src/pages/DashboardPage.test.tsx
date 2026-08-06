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
    getHealth: vi.fn(),
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
  confirmed_count: 0,
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
  vi.mocked(dashboardApi.getHealth).mockResolvedValue(
    (overrides.health as never) ?? {
      scanner_running: false,
      database_reachable: true,
      telegram_enabled: false,
      websocket_enabled: false,
      server_time_utc: "2026-01-01T10:00:00Z",
      started_at_utc: "2026-01-01T09:00:00Z",
    },
  );
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
      status: "CONFIRMED",
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
    mockAllEndpoints({ summary: { ...emptySummary, total_signals: 324, confirmed_count: 5 } });
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
          status: "CONFIRMED",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
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
      status: "CONFIRMED",
      entry_price: 116980,
      stop_loss: 116250,
      take_profit: 118200,
      risk_reward_ratio: 2.5,
      liquidity_type: "EQUAL_HIGH",
      entry_zone_type: "ORDER_BLOCK",
      structure_confirmation: "BOS",
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
          status: "CONFIRMED",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
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
          status: "CONFIRMED",
          entry_price: 116980,
          take_profit: 118200,
          stop_loss: 116250,
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
      status: "CONFIRMED",
      entry_price: 116980,
      take_profit: 118200,
      stop_loss: 116250,
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
        status: "CONFIRMED",
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
        price: 65000,
        direction: "BUY",
        score: 2,
        status: "REJECTED",
        failed_layer: "HTF_BIAS",
        reason: "ATR expansion ratio is insufficient.",
        updated_at_utc: "2026-01-01T10:00:15Z",
        validation_progress_raw_score: 2,
        validation_progress_max_score: 6,
        validation_progress_percentage: 33,
        last_executed_layer: "HTF_BIAS",
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
