import type { ActiveSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { ProgressBar } from "./ProgressBar";
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
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-slate-400">
            <th className="pb-2 font-medium">Coin</th>
            <th className="pb-2 font-medium">Price</th>
            <th className="pb-2 font-medium">Entry</th>
            <th className="pb-2 font-medium">TP</th>
            <th className="pb-2 font-medium">SL</th>
            <th className="pb-2 font-medium">Dist to TP</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {signals.map((signal) => (
            <tr key={signal.trade_id}>
              <td className="py-2.5">
                <div className="flex items-center gap-2 font-medium text-slate-900">
                  {signal.coin}
                  <DirectionBadge direction={signal.direction} />
                </div>
              </td>
              <td className="py-2.5 text-slate-700">{formatPriceOrDash(signal.current_price)}</td>
              <td className="py-2.5 text-slate-600">{formatPriceOrDash(signal.entry_price)}</td>
              <td className="py-2.5 font-medium text-emerald-600">
                {formatPriceOrDash(signal.take_profit)}
              </td>
              <td className="py-2.5 font-medium text-red-600">{formatPriceOrDash(signal.stop_loss)}</td>
              <td className="py-2.5">
                <div className="flex items-center gap-2">
                  <ProgressBar percentage={signal.distance_to_take_profit_percentage} />
                  <span className="text-emerald-600">
                    {formatPercentageOrDash(signal.distance_to_take_profit_percentage)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
