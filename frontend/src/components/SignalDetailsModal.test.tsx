import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SignalDetailsModal } from "./SignalDetailsModal";
import type { SignalDetails } from "../types/dashboard";

function details(overrides: Partial<SignalDetails> = {}): SignalDetails {
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

function renderModal(overrides: Partial<Parameters<typeof SignalDetailsModal>[0]> = {}) {
  return render(
    <SignalDetailsModal
      details={details()}
      loading={false}
      error={null}
      onClose={vi.fn()}
      onTrade={vi.fn()}
      trading={false}
      tradeError={null}
      {...overrides}
    />,
  );
}

describe("SignalDetailsModal", () => {
  it("preserves every existing field exactly", () => {
    renderModal();
    expect(screen.getByText("SMC-1")).toBeInTheDocument();
    expect(screen.getByText("BTC-USDT")).toBeInTheDocument();
    expect(screen.getByText("PREMIUM")).toBeInTheDocument();
    expect(screen.getByText("1:2.50")).toBeInTheDocument();
    expect(screen.getByText("96.0%")).toBeInTheDocument();
    expect(screen.getByText("TRENDING")).toBeInTheDocument();
    expect(screen.getByText("BULLISH")).toBeInTheDocument();
    expect(screen.getByText("Confirmed setup facts only.")).toBeInTheDocument();
  });

  it("shows a Trade button for a PREMIUM signal", () => {
    renderModal({ details: details({ signal_type: "PREMIUM" }) });
    expect(screen.getByRole("button", { name: "Trade" })).toBeInTheDocument();
  });

  it("shows a Trade button for a STRONG signal", () => {
    renderModal({ details: details({ signal_type: "STRONG" }) });
    expect(screen.getByRole("button", { name: "Trade" })).toBeInTheDocument();
  });

  it("does not show a Trade button for a non-PREMIUM/STRONG signal type", () => {
    renderModal({ details: details({ signal_type: "MEDIUM" as never }) });
    expect(screen.queryByRole("button", { name: "Trade" })).not.toBeInTheDocument();
  });

  it("still shows the Close button alongside Trade", () => {
    renderModal();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trade" })).toBeInTheDocument();
  });

  it("calls onTrade with the trade_id when Trade is clicked", async () => {
    const onTrade = vi.fn();
    renderModal({ onTrade });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Trade" }));
    expect(onTrade).toHaveBeenCalledWith("SMC-1");
  });

  it("shows 'Already Active' and disables the button when already active", () => {
    renderModal({ details: details({ dashboard_status: "ACTIVE" }) });
    const button = screen.getByRole("button", { name: "Already Active" });
    expect(button).toBeDisabled();
  });

  it("does not call onTrade when the Already Active button is clicked", async () => {
    const onTrade = vi.fn();
    renderModal({ details: details({ dashboard_status: "ACTIVE" }), onTrade });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Already Active" }));
    expect(onTrade).not.toHaveBeenCalled();
  });

  it("shows 'Activating…' and disables the button while trading", () => {
    renderModal({ trading: true });
    const button = screen.getByRole("button", { name: "Activating…" });
    expect(button).toBeDisabled();
  });

  it("shows a trade error message when present", () => {
    renderModal({ tradeError: "Failed to activate signal." });
    expect(screen.getByText("Failed to activate signal.")).toBeInTheDocument();
  });

  it("never renders a buy now / sell now execution button", () => {
    renderModal();
    expect(screen.queryByRole("button", { name: /buy now/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sell now/i })).not.toBeInTheDocument();
  });
});
