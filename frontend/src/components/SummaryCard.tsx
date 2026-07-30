import type { ReactNode } from "react";
import { formatPercentageOrDash } from "../utils/format";

interface SummaryCardProps {
  label: string;
  value: string;
  icon: ReactNode;
  iconClassName: string;
  changePercentage?: number | null;
  changeLabel?: string;
}

export function SummaryCard({
  label,
  value,
  icon,
  iconClassName,
  changePercentage,
  changeLabel = "vs last 30d",
}: SummaryCardProps) {
  const hasChange = changePercentage !== undefined && changePercentage !== null;
  const isPositive = hasChange && changePercentage! >= 0;

  return (
    <div className="flex-1 rounded-card border border-slate-200 bg-white p-2 shadow-card">
      <div className="mb-1 flex items-center gap-1.5">
        <span
          className={`flex h-6 w-6 items-center justify-center rounded-lg ${iconClassName}`}
        >
          {icon}
        </span>
        <span className="text-xs font-medium text-slate-500">{label}</span>
      </div>
      <div className="text-lg font-bold text-slate-900">{value}</div>
      {hasChange ? (
        <div className="mt-0.5 flex items-center gap-1 text-xs">
          <span className={isPositive ? "font-medium text-emerald-600" : "font-medium text-red-600"}>
            {isPositive ? "↗" : "↘"} {formatPercentageOrDash(Math.abs(changePercentage!), 0)}
          </span>
          <span className="text-slate-400">{changeLabel}</span>
        </div>
      ) : (
        <div className="mt-0.5 text-xs text-slate-300">No data</div>
      )}
    </div>
  );
}
