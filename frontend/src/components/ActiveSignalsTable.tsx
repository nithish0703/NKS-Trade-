import type { ActiveSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { CircularProgress } from "./CircularProgress";
import { SignalField } from "./SignalField";
import { EmptyState } from "./EmptyState";
import { formatPercentageOrDash, formatPriceOrDash } from "../utils/format";

interface ActiveSignalsTableProps {
  signals: ActiveSignal[];
}

export function ActiveSignalsTable({ signals }: ActiveSignalsTableProps) {
  if (signals.length === 0) {
    return <EmptyState message="No active signals" />;
  }

  return (
    <div className="space-y-3">
      {signals.map((signal) => {
        const distance = signal.distance_to_take_profit_percentage;
        return (
          <div
            key={signal.trade_id}
            className="rounded-lg border border-slate-100 p-3"
          >
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-900">{signal.coin}</span>
                <DirectionBadge direction={signal.direction} />
              </div>
              <span className="inline-flex items-center rounded-md bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
                ACTIVE
              </span>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-4">
                <SignalField label="Price" value={formatPriceOrDash(signal.current_price)} />
                <SignalField label="Entry" value={formatPriceOrDash(signal.entry_price)} />
                <SignalField label="TP" value={formatPriceOrDash(signal.take_profit)} valueClassName="text-emerald-600" />
                <SignalField label="SL" value={formatPriceOrDash(signal.stop_loss)} valueClassName="text-red-600" />
              </div>

              <div className="flex items-center gap-2">
                <CircularProgress
                  percentage={distance}
                  colorClassName={distance !== null && distance > 0 ? "text-emerald-500" : "text-slate-300"}
                />
                <div className="flex flex-col">
                  <span className="text-[11px] uppercase tracking-wide text-slate-400">Dist to TP</span>
                  <span className="text-sm font-semibold text-emerald-600">
                    {formatPercentageOrDash(distance)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
