import { useState } from "react";
import { Search } from "lucide-react";
import type { ScanningCoin } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { CircularProgress } from "./CircularProgress";
import { EmptyState } from "./EmptyState";

interface ScanningCoinsTableProps {
  coins: ScanningCoin[];
}

const PREVIEW_TOOLTIP_NOTICE = "Dashboard preview only. This is not final trade confidence.";

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

export function ScanningCoinsTable({ coins }: ScanningCoinsTableProps) {
  const [searchTerm, setSearchTerm] = useState("");

  if (coins.length === 0) {
    return <EmptyState message="Waiting for scanner" />;
  }

  const sortedCoins = [...coins].sort(
    (a, b) => (b.preview_progress_percentage ?? -1) - (a.preview_progress_percentage ?? -1),
  );

  const filteredCoins = sortedCoins.filter((coin) =>
    coin.coin.toLowerCase().includes(searchTerm.trim().toLowerCase()),
  );

  return (
    <div>
      <div className="relative mb-3 w-48">
        <Search
          size={14}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          type="text"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Search coin..."
          aria-label="Search coin"
          className="w-full rounded-md border border-slate-200 py-1.5 pl-8 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-purple-400 focus:outline-none focus:ring-1 focus:ring-purple-400"
        />
      </div>

      {filteredCoins.length === 0 ? (
        <EmptyState message="No coins match your search" />
      ) : (
        <div className="max-h-[440px] overflow-y-auto overflow-x-auto">
          <table className="w-full table-auto border-collapse text-left text-sm">
            <thead className="sticky top-0 z-10 bg-white">
              <tr className="text-xs uppercase tracking-wide text-slate-400">
                <th className="w-auto pb-2 pr-4 font-medium">Coin</th>
                <th className="pb-2 pr-6 font-medium">Direction</th>
                <th className="pb-2 font-medium">Score (%)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredCoins.map((coin) => {
                const percentage = coin.preview_progress_percentage ?? 0;
                return (
                  <tr key={coin.coin}>
                    <td className="py-1 pr-4 font-medium text-slate-900">{coin.coin}</td>
                    <td className="py-1 pr-6">
                      <DirectionBadge direction={coin.preview_direction} />
                    </td>
                    <td className="py-1">
                      <div className="flex items-center" title={scoreTooltip(coin)}>
                        <CircularProgress
                          percentage={coin.preview_progress_percentage === null ? null : percentage}
                          colorClassName={progressColorClassName(percentage)}
                          size={46}
                          showLabel
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
