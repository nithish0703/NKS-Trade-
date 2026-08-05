import type { ActiveSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { CircularProgress } from "./CircularProgress";
import { SignalField } from "./SignalField";
import { SignalStatusBadge } from "./SignalStatusBadge";
import { EmptyState } from "./EmptyState";
import { formatPercentageOrDash, formatPriceOrDash } from "../utils/format";

interface ActiveSignalsTableProps {
  signals: ActiveSignal[];
}

export function ActiveSignalsTable({ signals }: ActiveSignalsTableProps) {
  if (signals.length === 0) {
    return <EmptyState message="No active signals" />;
  }

  const isSingleSignal = signals.length === 1;

  return (
    <div className="space-y-3">
      {signals.map((signal) => {
        const distance = signal.distance_to_take_profit_percentage;
        return (
          <div
            key={signal.trade_id}
            className={`rounded-lg border border-slate-100 ${isSingleSignal ? "p-6" : "p-3"}`}
          >
            <div className={`flex items-center justify-between ${isSingleSignal ? "mb-5" : "mb-2"}`}>
              <div className="flex items-center gap-2">
                <span className={`font-semibold text-slate-900 ${isSingleSignal ? "text-xl" : ""}`}>
                  {signal.coin}
                </span>
                <DirectionBadge direction={signal.direction} />
              </div>
              <span
                className={`inline-flex items-center rounded-md bg-emerald-50 font-medium text-emerald-700 ${
                  isSingleSignal ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs"
                }`}
              >
                ACTIVE
              </span>
            </div>

            <div
              className={`flex flex-wrap items-center justify-between gap-3 ${
                isSingleSignal ? "gap-y-6" : ""
              }`}
            >
              <div className={`flex flex-wrap ${isSingleSignal ? "gap-8" : "gap-4"}`}>
                <SignalField
                  label="Price"
                  value={formatPriceOrDash(signal.current_price)}
                  valueClassName={isSingleSignal ? "text-slate-900 text-lg" : undefined}
                />
                <SignalField
                  label="Entry"
                  value={formatPriceOrDash(signal.entry_price)}
                  valueClassName={isSingleSignal ? "text-slate-900 text-lg" : undefined}
                />
                <SignalField
                  label="TP"
                  value={formatPriceOrDash(signal.take_profit)}
                  valueClassName={isSingleSignal ? "text-emerald-600 text-lg" : "text-emerald-600"}
                />
                <SignalField
                  label="SL"
                  value={formatPriceOrDash(signal.stop_loss)}
                  valueClassName={isSingleSignal ? "text-red-600 text-lg" : "text-red-600"}
                />
                <div className="flex flex-col">
                  <span className="text-[11px] uppercase tracking-wide text-slate-400">Status</span>
                  <SignalStatusBadge status={signal.status} />
                </div>
              </div>

              <div className="flex items-center gap-3">
                <CircularProgress
                  percentage={distance}
                  colorClassName={distance !== null && distance > 0 ? "text-emerald-500" : "text-slate-300"}
                  size={isSingleSignal ? 46 : 20}
                  showLabel={isSingleSignal}
                />
                <div className="flex flex-col">
                  <span className="text-[11px] uppercase tracking-wide text-slate-400">Dist to TP</span>
                  <span className={`font-semibold text-emerald-600 ${isSingleSignal ? "text-lg" : "text-sm"}`}>
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
