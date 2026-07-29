import type { PremiumSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { EmptyState } from "./EmptyState";
import { formatPriceOrDash } from "../utils/format";

interface PremiumSignalsTableProps {
  signals: PremiumSignal[];
  onView: (tradeId: string) => void;
}

export function PremiumSignalsTable({ signals, onView }: PremiumSignalsTableProps) {
  if (signals.length === 0) {
    return <EmptyState message="No Premium signals yet" />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-slate-400">
            <th className="pb-2 font-medium">Coin</th>
            <th className="pb-2 font-medium">Dir</th>
            <th className="pb-2 font-medium">Tier</th>
            <th className="pb-2 font-medium">Entry</th>
            <th className="pb-2 font-medium">TP</th>
            <th className="pb-2 font-medium">SL</th>
            <th className="pb-2 font-medium">Confidence</th>
            <th className="pb-2 font-medium">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {signals.map((signal) => (
            <tr key={signal.trade_id}>
              <td className="py-2.5 font-medium text-slate-900">{signal.coin}</td>
              <td className="py-2.5">
                <DirectionBadge direction={signal.direction} />
              </td>
              <td className="py-2.5">
                <span className="inline-flex items-center rounded-md bg-purple-50 px-2 py-0.5 text-xs font-semibold text-purple-700">
                  PREMIUM
                </span>
              </td>
              <td className="py-2.5 text-slate-600">{formatPriceOrDash(signal.entry_price)}</td>
              <td className="py-2.5 text-emerald-600">{formatPriceOrDash(signal.take_profit)}</td>
              <td className="py-2.5 text-red-600">{formatPriceOrDash(signal.stop_loss)}</td>
              <td className="py-2.5">
                <ConfidenceBadge score={signal.confidence_score} tier="PREMIUM" />
              </td>
              <td className="py-2.5">
                <button
                  type="button"
                  onClick={() => onView(signal.trade_id)}
                  className="rounded-md bg-purple-600 px-3 py-1 text-xs font-semibold text-white hover:bg-purple-700"
                >
                  View
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
