import type { ScanningCoin } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { CircularProgress } from "./CircularProgress";
import { EmptyState } from "./EmptyState";

interface ScanningCoinsTableProps {
  coins: ScanningCoin[];
}

const PREVIEW_TOOLTIP_NOTICE = "Dashboard preview only. This is not final trade confidence.";
const CHART_TREND_TOOLTIP =
  "Chart trend: raw price direction of the last completed candle vs. the previous one. Not a trade signal.";

function progressColorClassName(percentage: number): string {
  return percentage > 0 ? "text-emerald-500" : "text-slate-300";
}

function scoreTooltip(coin: ScanningCoin): string {
  const lines = [
    PREVIEW_TOOLTIP_NOTICE,
    `Preview progress: ${coin.preview_progress_raw_score ?? 0} / ${
      coin.preview_progress_max_score ?? 120
    }`,
    `Completed layers: ${coin.preview_completed_layers?.join(", ") || "—"}`,
    `Failed layers: ${coin.preview_failed_layers?.join(", ") || "—"}`,
    `Failed layer (real pipeline): ${coin.failed_layer ?? "—"}`,
    `Reason: ${coin.reason ?? "—"}`,
  ];
  return lines.join("\n");
}

function ChartTrendIndicator({ trend }: { trend: ScanningCoin["chart_trend"] }) {
  if (trend !== "UP" && trend !== "DOWN") {
    return <span className="text-sm text-slate-400">—</span>;
  }
  const isUp = trend === "UP";
  return (
    <span
      title={CHART_TREND_TOOLTIP}
      className={`text-sm font-semibold ${isUp ? "text-emerald-600" : "text-red-600"}`}
    >
      {isUp ? "↑" : "↓"}
    </span>
  );
}

export function ScanningCoinsTable({ coins }: ScanningCoinsTableProps) {
  if (coins.length === 0) {
    return <EmptyState message="Waiting for scanner" />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full table-auto border-collapse text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-slate-400">
            <th className="w-auto pb-2 pr-2 font-medium">Coin</th>
            <th className="pb-2 pr-4 font-medium">Direction</th>
            <th className="pb-2 pr-4 font-medium">Chart</th>
            <th className="pb-2 font-medium">Score (%)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {coins.map((coin) => {
            const percentage = coin.preview_progress_percentage ?? 0;
            return (
              <tr key={coin.coin}>
                <td className="py-2.5 pr-2 font-medium text-slate-900">{coin.coin}</td>
                <td className="py-2.5 pr-4">
                  <DirectionBadge direction={coin.preview_direction} />
                </td>
                <td className="py-2.5 pr-4">
                  <ChartTrendIndicator trend={coin.chart_trend} />
                </td>
                <td className="py-2.5">
                  <div
                    className="flex items-center gap-2 text-slate-600"
                    title={scoreTooltip(coin)}
                  >
                    <span className="w-9 tabular-nums">
                      {coin.preview_progress_percentage === null ? "—" : `${percentage}%`}
                    </span>
                    <CircularProgress
                      percentage={percentage}
                      colorClassName={progressColorClassName(percentage)}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
