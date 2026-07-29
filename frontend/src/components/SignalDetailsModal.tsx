import { X } from "lucide-react";
import type { SignalDetails } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { formatPriceOrDash, formatUtcTime } from "../utils/format";

interface SignalDetailsModalProps {
  details: SignalDetails | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-slate-100 py-2 text-sm last:border-0">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-900">{value}</span>
    </div>
  );
}

export function SignalDetailsModal({ details, loading, error, onClose }: SignalDetailsModalProps) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Signal details"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-card bg-white p-6 shadow-card"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-slate-900">Signal Details</h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100"
          >
            <X size={16} />
          </button>
        </div>

        {loading ? <div className="py-8 text-center text-sm text-slate-400">Loading…</div> : null}
        {error ? <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}

        {details ? (
          <div>
            <Row label="Trade ID" value={details.trade_id} />
            <Row label="Coin" value={details.coin} />
            <Row label="Direction" value={<DirectionBadge direction={details.direction} />} />
            <Row label="Signal Type" value={details.signal_type} />
            <Row label="Entry" value={formatPriceOrDash(details.entry_price)} />
            <Row label="Dynamic Stop Loss" value={formatPriceOrDash(details.stop_loss)} />
            <Row label="Take Profit" value={formatPriceOrDash(details.take_profit)} />
            <Row label="Risk Reward" value={`1:${details.risk_reward_ratio.toFixed(2)}`} />
            <Row label="Confidence" value={`${details.confidence_score.toFixed(1)}%`} />
            <Row label="Market Regime" value={details.market_regime} />
            <Row label="Higher Timeframe Bias" value={details.higher_timeframe_bias} />
            <Row label="Liquidity Type" value={details.liquidity_type} />
            <Row label="Entry Zone Type" value={details.entry_zone_type} />
            <Row label="Structure Confirmation" value={details.structure_confirmation} />
            <Row label="Volume Confirmation" value={details.volume_confirmation ? "Confirmed" : "Not confirmed"} />
            <Row label="ATR Status" value={details.atr_status} />
            <Row label="Trading Session" value={details.trading_session} />
            <Row label="BTC Alignment" value={details.btc_market_alignment ? "Aligned" : "Not aligned"} />
            <Row label="Detection Time" value={formatUtcTime(details.detection_time_utc)} />
            <div className="mt-3 rounded-md bg-slate-50 p-3 text-sm text-slate-600">
              <div className="mb-1 font-semibold text-slate-700">Institutional Reason</div>
              {details.institutional_reason}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
