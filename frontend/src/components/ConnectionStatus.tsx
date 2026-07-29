interface ConnectionStatusProps {
  isConnected: boolean;
}

export function ConnectionStatus({ isConnected }: ConnectionStatusProps) {
  return (
    <div className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600">
      <span
        className={`h-2 w-2 rounded-full ${isConnected ? "bg-emerald-500" : "bg-slate-300"}`}
        data-testid="connection-dot"
      />
      {isConnected ? "Live" : "Offline"}
    </div>
  );
}
