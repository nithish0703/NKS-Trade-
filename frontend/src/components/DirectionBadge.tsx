interface DirectionBadgeProps {
  direction: "BUY" | "SELL" | null;
}

export function DirectionBadge({ direction }: DirectionBadgeProps) {
  if (!direction) {
    return <span className="text-sm text-slate-400">—</span>;
  }

  const isBuy = direction === "BUY";
  return (
    <span
      className={`text-sm font-semibold ${isBuy ? "text-emerald-600" : "text-red-600"}`}
    >
      {direction}
    </span>
  );
}
