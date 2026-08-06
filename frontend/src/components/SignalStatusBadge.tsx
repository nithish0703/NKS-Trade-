interface SignalStatusBadgeProps {
  status: string | null;
}

export function SignalStatusBadge({ status }: SignalStatusBadgeProps) {
  if (status === null) {
    return (
      <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-500">
        Premium only
      </span>
    );
  }

  const colorClass =
    status === "CONFIRMED" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700";

  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ${colorClass}`}>
      {status}
    </span>
  );
}
