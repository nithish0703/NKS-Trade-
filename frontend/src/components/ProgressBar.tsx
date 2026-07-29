interface ProgressBarProps {
  percentage: number | null;
  colorClassName?: string;
}

export function ProgressBar({ percentage, colorClassName = "bg-emerald-500" }: ProgressBarProps) {
  const clamped = percentage === null ? 0 : Math.max(0, Math.min(100, percentage));
  return (
    <div className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-100">
      <div
        className={`h-full rounded-full ${colorClassName}`}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
