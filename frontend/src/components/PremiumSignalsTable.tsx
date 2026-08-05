import type { PremiumSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { SignalStatusBadge } from "./SignalStatusBadge";
import { SignalField } from "./SignalField";
import { EmptyState } from "./EmptyState";
import { formatIstTime, formatPriceOrDash } from "../utils/format";

interface PremiumSignalsTableProps {
  signals: PremiumSignal[];
  onView: (tradeId: string) => void;
}

export function PremiumSignalsTable({ signals, onView }: PremiumSignalsTableProps) {
  if (signals.length === 0) {
    return <EmptyState message="No Premium signals yet" />;
  }

  return (
    <div className="space-y-3">
      {signals.map((signal) => (
        <div key={signal.trade_id} className="rounded-lg border border-slate-100 p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-slate-900">{signal.coin}</span>
              <DirectionBadge direction={signal.direction} />
            </div>
            <button
              type="button"
              onClick={() => onView(signal.trade_id)}
              className="rounded-md bg-purple-600 px-3 py-1 text-xs font-semibold text-white hover:bg-purple-700"
            >
              Go
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <SignalField label="Entry" value={formatPriceOrDash(signal.entry_price)} />
            <SignalField label="TP" value={formatPriceOrDash(signal.take_profit)} valueClassName="text-emerald-600" />
            <SignalField label="SL" value={formatPriceOrDash(signal.stop_loss)} valueClassName="text-red-600" />
            <div className="flex flex-col">
              <span className="text-[11px] uppercase tracking-wide text-slate-400">Status</span>
              <SignalStatusBadge status={signal.status} />
            </div>
          </div>

          <div className="mt-2 text-xs text-slate-400">
            Signal given: {formatIstTime(signal.detection_time_utc)}
          </div>
        </div>
      ))}
    </div>
  );
}
