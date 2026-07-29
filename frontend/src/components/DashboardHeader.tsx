import { ConnectionStatus } from "./ConnectionStatus";
import { useUtcClock } from "../hooks/useUtcClock";
import { formatIstClock, formatUtcClock } from "../utils/format";

interface DashboardHeaderProps {
  isConnected: boolean;
}

export function DashboardHeader({ isConnected }: DashboardHeaderProps) {
  const now = useUtcClock();

  return (
    <header className="flex items-center justify-between px-6 py-5">
      <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
      <div className="flex items-center gap-3">
        <ConnectionStatus isConnected={isConnected} />
        <div className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600">
          {formatUtcClock(now)}
        </div>
        <div className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600">
          {formatIstClock(now)}
        </div>
      </div>
    </header>
  );
}
