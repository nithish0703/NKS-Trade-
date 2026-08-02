import { ConnectionStatus } from "./ConnectionStatus";
import { useUtcClock } from "../hooks/useUtcClock";
import {
  formatIstClock,
  formatRelativeTime,
  formatUptime,
  formatUtcClock,
  formatUtcTime,
} from "../utils/format";

interface DashboardHeaderProps {
  isConnected: boolean;
  lastScanTimeUtc?: string | null;
  serverStartedAtUtc?: string | null;
}

export function DashboardHeader({
  isConnected,
  lastScanTimeUtc,
  serverStartedAtUtc,
}: DashboardHeaderProps) {
  const now = useUtcClock();

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 px-4 py-5 sm:px-6">
      <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <ConnectionStatus isConnected={isConnected} />
        <div
          className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600"
          title={
            serverStartedAtUtc
              ? `API started at ${formatUtcTime(serverStartedAtUtc)}`
              : "API start time unavailable."
          }
        >
          Uptime: {formatUptime(serverStartedAtUtc, now)}
        </div>
        <div
          className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-600"
          title={lastScanTimeUtc ? formatUtcTime(lastScanTimeUtc) : "No scan has completed yet."}
        >
          Last scan: {formatRelativeTime(lastScanTimeUtc, now)}
        </div>
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
