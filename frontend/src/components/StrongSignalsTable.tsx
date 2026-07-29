import type { StrongSignal } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { EmptyState } from "./EmptyState";

interface StrongSignalsTableProps {
  signals: StrongSignal[];
}

export function StrongSignalsTable({ signals }: StrongSignalsTableProps) {
  if (signals.length === 0) {
    return <EmptyState message="No Strong signals yet" />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-slate-400">
            <th className="pb-2 font-medium">Coin</th>
            <th className="pb-2 font-medium">Dir</th>
            <th className="pb-2 font-medium">Confidence</th>
            <th className="pb-2 font-medium">HTF Bias</th>
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
                <ConfidenceBadge score={signal.confidence_score} tier="STRONG" />
              </td>
              <td className="py-2.5 text-slate-600">{signal.higher_timeframe_bias}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
