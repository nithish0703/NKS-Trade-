interface SignalFieldProps {
  label: string;
  value: string;
  valueClassName?: string;
}

export function SignalField({ label, value, valueClassName = "text-slate-900" }: SignalFieldProps) {
  return (
    <div className="flex min-w-[64px] flex-col">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">{label}</span>
      <span className={`text-sm font-semibold ${valueClassName}`}>{value}</span>
    </div>
  );
}
