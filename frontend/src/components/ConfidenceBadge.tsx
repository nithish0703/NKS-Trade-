import { formatPercentageOrDash } from "../utils/format";

interface ConfidenceBadgeProps {
  score: number | null;
  tier?: "PREMIUM" | "STRONG";
}

export function ConfidenceBadge({ score, tier }: ConfidenceBadgeProps) {
  const colorClass =
    tier === "PREMIUM"
      ? "bg-purple-50 text-purple-700"
      : tier === "STRONG"
        ? "bg-blue-50 text-blue-700"
        : "bg-emerald-50 text-emerald-700";

  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${colorClass}`}>
      {formatPercentageOrDash(score, 0)}
    </span>
  );
}
