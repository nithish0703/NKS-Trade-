import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import type { ScanningCoin } from "../types/dashboard";
import { DirectionBadge } from "./DirectionBadge";
import { CircularProgress } from "./CircularProgress";
import { EmptyState } from "./EmptyState";
import { formatPriceOrDash } from "../utils/format";

interface ScanningCoinsTableProps {
  coins: ScanningCoin[];
}

// Row order is only recomputed on this cadence, not on every render.
// WebSocket score updates can fire many times per scan cycle; if the
// table resorted every time one arrived, rows would keep swapping
// places and the table would appear to flicker/blink. Freezing the
// order between ticks lets cell values (price, score, status) keep
// updating live in place while the ranking itself only resettles
// every few seconds.
const SORT_STABILIZATION_INTERVAL_MS = 5000;

function scoreRank(coin: ScanningCoin): number {
  // Higher stage-pass score ranks first. Coins with no score yet sink
  // to the bottom instead of jumping to the top. Ranking-only: never
  // used to generate a signal.
  return coin.score ?? -1;
}

function compareByScoreThenCoin(a: ScanningCoin, b: ScanningCoin): number {
  const scoreDiff = scoreRank(b) - scoreRank(a);
  if (scoreDiff !== 0) {
    return scoreDiff;
  }
  // Deterministic tie-break so equal scores don't reorder randomly
  // between stabilization ticks.
  return a.coin.localeCompare(b.coin);
}

const SCORE_TOOLTIP_NOTICE = "Ranking only. This is not a trading signal or confidence score.";

function progressColorClassName(percentage: number): string {
  return percentage > 0 ? "text-emerald-500" : "text-slate-300";
}

function scoreTooltip(coin: ScanningCoin): string {
  const lines = [
    SCORE_TOOLTIP_NOTICE,
    `Stages cleared: ${coin.validation_progress_raw_score ?? 0} / ${
      coin.validation_progress_max_score ?? 0
    }`,
    `Last executed stage: ${coin.last_executed_layer ?? "—"}`,
    `Failed layer: ${coin.failed_layer ?? "—"}`,
    `Reason: ${coin.reason ?? "—"}`,
  ];
  return lines.join("\n");
}

const INSUFFICIENT_CANDLES_PATTERN = /insufficient candles.*?a (\d+)-period (\w+)/i;

function shortErrorReason(reason: string | null): string | null {
  if (!reason) {
    return null;
  }
  // The most common ERROR case by far is a newly listed pair that
  // doesn't yet have enough historical candles for a long-lookback
  // indicator (e.g. EMA200 needs ~200 4h candles, roughly a month of
  // trading history) -- summarize that specific, expected case in a
  // few words instead of the full backend diagnostic sentence.
  const match = reason.match(INSUFFICIENT_CANDLES_PATTERN);
  if (match) {
    return `not enough history for ${match[2]} (needs ${match[1]} candles)`;
  }
  return reason;
}

function statusLabel(coin: ScanningCoin): string {
  if (coin.status === "REJECTED") {
    return coin.failed_layer ? `REJECTED: ${coin.failed_layer}` : "REJECTED";
  }
  if (coin.status === "ERROR") {
    const shortReason = shortErrorReason(coin.reason);
    return shortReason ? `ERROR: ${shortReason}` : "ERROR";
  }
  return coin.status;
}

function statusColorClassName(status: ScanningCoin["status"]): string {
  switch (status) {
    case "READY":
      return "text-emerald-600";
    case "REJECTED":
      return "text-red-600";
    case "ERROR":
      return "text-red-600";
    case "DUPLICATE":
      return "text-amber-600";
    default:
      return "text-slate-400";
  }
}

export function scanningCoinsSummary(coins: ScanningCoin[]): { total: number; scanning: number } {
  return {
    total: coins.length,
    scanning: coins.filter((coin) => coin.status === "SCANNING").length,
  };
}

export function ScanningCoinsTable({ coins }: ScanningCoinsTableProps) {
  const [searchTerm, setSearchTerm] = useState("");

  // Snapshot of coin symbols in ranked order, refreshed only on the
  // stabilization interval below (see SORT_STABILIZATION_INTERVAL_MS).
  const [orderedKeys, setOrderedKeys] = useState<string[]>(() =>
    [...coins].sort(compareByScoreThenCoin).map((coin) => coin.coin),
  );
  const coinsRef = useRef(coins);
  coinsRef.current = coins;

  useEffect(() => {
    const recompute = () => {
      setOrderedKeys([...coinsRef.current].sort(compareByScoreThenCoin).map((coin) => coin.coin));
    };
    const interval = setInterval(recompute, SORT_STABILIZATION_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  if (coins.length === 0) {
    return <EmptyState message="Waiting for scanner" />;
  }

  const coinByKey = new Map(coins.map((coin) => [coin.coin, coin]));
  // Coins already placed by the last stabilization tick keep their
  // slot; a coin the scanner has newly discovered since that tick is
  // appended at the end rather than forcing an immediate resort, and
  // one that has dropped out of the feed is simply skipped.
  const knownKeys = orderedKeys.filter((key) => coinByKey.has(key));
  const knownKeySet = new Set(knownKeys);
  const newKeys = coins.map((coin) => coin.coin).filter((key) => !knownKeySet.has(key));
  const sortedCoins = [...knownKeys, ...newKeys].map((key) => coinByKey.get(key)!);

  const filteredCoins = sortedCoins.filter((coin) =>
    coin.coin.toLowerCase().includes(searchTerm.trim().toLowerCase()),
  );

  const { total, scanning } = scanningCoinsSummary(coins);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between text-xs text-slate-500">
        <span>
          <span className="font-semibold text-slate-900">{total}</span> total coin
          {total === 1 ? "" : "s"}
        </span>
        {scanning > 0 ? (
          <span>
            <span className="font-semibold text-purple-600">{scanning}</span> scanning
          </span>
        ) : null}
      </div>

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
                <th className="pb-2 pr-6 font-medium">Live Price</th>
                <th className="pb-2 pr-6 font-medium">Direction</th>
                <th className="pb-2 pr-6 font-medium">Score (%)</th>
                <th className="pb-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredCoins.map((coin) => {
                const percentage = coin.validation_progress_percentage ?? 0;
                return (
                  <tr key={coin.coin}>
                    <td className="py-1 pr-4 font-medium text-slate-900">{coin.coin}</td>
                    <td className="py-1 pr-6 tabular-nums text-slate-700">
                      {formatPriceOrDash(coin.price)}
                    </td>
                    <td className="py-1 pr-6">
                      <DirectionBadge direction={coin.direction} />
                    </td>
                    <td className="py-1 pr-6">
                      <div className="flex items-center" title={scoreTooltip(coin)}>
                        <CircularProgress
                          percentage={coin.validation_progress_percentage === null ? null : percentage}
                          colorClassName={progressColorClassName(percentage)}
                          size={46}
                          showLabel
                        />
                      </div>
                    </td>
                    <td
                      className={`py-1 text-xs ${statusColorClassName(coin.status)}`}
                      title={scoreTooltip(coin)}
                    >
                      {statusLabel(coin)}
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
