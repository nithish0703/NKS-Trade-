import { ConnectionStatus } from "./ConnectionStatus";
import { useUtcClock } from "../hooks/useUtcClock";
import { formatIstClock, formatUtcClock } from "../utils/format";

interface DashboardHeaderProps {
  isConnected: boolean;
}

export function DashboardHeader({ isConnected }: DashboardHeaderProps) {
  const now = useUtcClock();

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-5 sm:px-6">
      <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
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
