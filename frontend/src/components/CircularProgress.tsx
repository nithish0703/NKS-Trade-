interface CircularProgressProps {
  percentage: number | null;
  colorClassName?: string;
  size?: number;
  showLabel?: boolean;
}

export function CircularProgress({
  percentage,
  colorClassName = "text-emerald-500",
  size = 20,
  showLabel = false,
}: CircularProgressProps) {
  const clamped = percentage === null ? 0 : Math.max(0, Math.min(100, percentage));
  const strokeWidth = 3;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (clamped / 100) * circumference;
  const center = size / 2;

  return (
    <div className="relative inline-flex" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          className="stroke-slate-100"
        />
        <circle
          cx={center}
          cy={center}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className={colorClassName}
          stroke="currentColor"
        />
      </svg>
      {showLabel ? (
        <span
          className={`absolute inset-0 flex items-center justify-center text-[16px] font-semibold tabular-nums ${
            percentage === null ? "text-slate-400" : "text-slate-900"
          }`}
        >
          {percentage === null ? "--" : `${percentage}%`}
        </span>
      ) : null}
    </div>
  );
}
