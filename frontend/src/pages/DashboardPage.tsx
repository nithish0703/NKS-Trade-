import { useCallback, useState } from "react";
import { Activity, AlertOctagon, Gem, Scale, Trophy } from "lucide-react";
import { DashboardHeader } from "../components/DashboardHeader";
import { SummaryCard } from "../components/SummaryCard";
import { DataCard } from "../components/DataCard";
import { ScanningCoinsTable } from "../components/ScanningCoinsTable";
import { ActiveSignalsTable } from "../components/ActiveSignalsTable";
import { PremiumSignalsTable } from "../components/PremiumSignalsTable";
import { StrongSignalsTable } from "../components/StrongSignalsTable";
import { SignalDetailsModal } from "../components/SignalDetailsModal";
import { LoadingSkeleton } from "../components/LoadingSkeleton";
import { ErrorState } from "../components/ErrorState";
import { useApiResource } from "../hooks/useApiResource";
import { useDashboardSocket } from "../hooks/useDashboardSocket";
import { dashboardApi } from "../services/dashboard.api";
import { signalsApi } from "../services/signals.api";
import { formatNumberOrDash, formatPercentageOrDash } from "../utils/format";
import type { SignalDetails } from "../types/dashboard";

const REST_RECONCILE_INTERVAL_MS = 15_000;

export function DashboardPage() {
  const summary = useApiResource(dashboardApi.getSummary, REST_RECONCILE_INTERVAL_MS);
  const scanningCoins = useApiResource(dashboardApi.getScanningCoins, REST_RECONCILE_INTERVAL_MS);
  const activeSignals = useApiResource(dashboardApi.getActiveSignals, REST_RECONCILE_INTERVAL_MS);
  const premiumSignals = useApiResource(dashboardApi.getPremiumSignals, REST_RECONCILE_INTERVAL_MS);
  const strongSignals = useApiResource(dashboardApi.getStrongSignals, REST_RECONCILE_INTERVAL_MS);
  // started_at_utc never changes while the process is alive, so this
  // only needs to be fetched once per page load, not on the same 15s
  // poll as the rest of the dashboard's live data.
  const health = useApiResource(dashboardApi.getHealth, null);

  const handleSocketEvent = useCallback(() => {
    // Any scanner event can affect multiple cards; refresh the affected
    // REST resources rather than trying to hand-patch local state for
    // every event shape. The dashboard still avoids a full page reload.
    summary.refresh();
    scanningCoins.refresh();
    activeSignals.refresh();
    premiumSignals.refresh();
    strongSignals.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { isConnected } = useDashboardSocket(handleSocketEvent);

  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [selectedDetails, setSelectedDetails] = useState<SignalDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [trading, setTrading] = useState(false);
  const [tradeError, setTradeError] = useState<string | null>(null);

  const handleView = useCallback((tradeId: string) => {
    setSelectedTradeId(tradeId);
    setSelectedDetails(null);
    setDetailsError(null);
    setDetailsLoading(true);
    setTradeError(null);
    signalsApi
      .getSignalDetails(tradeId)
      .then((details) => setSelectedDetails(details))
      .catch((error: unknown) => {
        setDetailsError(error instanceof Error ? error.message : "Failed to load signal details.");
      })
      .finally(() => setDetailsLoading(false));
  }, []);

  const closeModal = useCallback(() => {
    setSelectedTradeId(null);
    setTradeError(null);
  }, []);

  const handleTrade = useCallback((tradeId: string) => {
    setTrading(true);
    setTradeError(null);
    dashboardApi
      .activateSignal(tradeId)
      .then(() => {
        // The signal has moved from Premium/Strong into Active Signals on
        // the backend; refresh every affected list so the dashboard
        // reflects the new state immediately, then close the modal.
        summary.refresh();
        activeSignals.refresh();
        premiumSignals.refresh();
        strongSignals.refresh();
        setSelectedTradeId(null);
      })
      .catch((error: unknown) => {
        setTradeError(error instanceof Error ? error.message : "Failed to activate signal.");
      })
      .finally(() => setTrading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 pb-10">
      <DashboardHeader
        isConnected={isConnected}
        lastScanTimeUtc={summary.data?.last_scan_time_utc}
        serverStartedAtUtc={health.data?.started_at_utc}
      />

      <main className="mx-auto max-w-[1400px] space-y-6 px-4 sm:px-6">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <SummaryCard
            label="Total Signals"
            value={formatNumberOrDash(summary.data?.total_signals ?? null)}
            icon={<Activity size={14} />}
            iconClassName="bg-purple-50 text-purple-600"
            changePercentage={summary.data?.comparison.total_signals_percentage}
          />
          <SummaryCard
            label="Wins"
            value={formatNumberOrDash(summary.data?.wins ?? null)}
            icon={<Trophy size={14} />}
            iconClassName="bg-emerald-50 text-emerald-600"
            changePercentage={summary.data?.comparison.wins_percentage}
          />
          <SummaryCard
            label="Losses"
            value={formatNumberOrDash(summary.data?.losses ?? null)}
            icon={<AlertOctagon size={14} />}
            iconClassName="bg-red-50 text-red-600"
            changePercentage={summary.data?.comparison.losses_percentage}
          />
          <SummaryCard
            label="Win Rate"
            value={formatPercentageOrDash(summary.data?.win_rate ?? null, 0)}
            icon={<Scale size={14} />}
            iconClassName="bg-blue-50 text-blue-600"
            changePercentage={summary.data?.comparison.win_rate_percentage}
          />
          <SummaryCard
            label="Avg RR"
            value={
              summary.data?.average_rr != null ? summary.data.average_rr.toFixed(2) : "—"
            }
            icon={<Gem size={14} />}
            iconClassName="bg-orange-50 text-orange-600"
            changePercentage={summary.data?.comparison.average_rr_percentage}
          />
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <DataCard
            title="Scanning Coins"
            icon={<Activity size={16} />}
            iconClassName="bg-purple-50 text-purple-600"
            className="lg:col-span-2"
          >
            {scanningCoins.loading ? (
              <LoadingSkeleton />
            ) : scanningCoins.error ? (
              <ErrorState message={scanningCoins.error} />
            ) : (
              <ScanningCoinsTable coins={scanningCoins.data ?? []} />
            )}
          </DataCard>

          <DataCard
            title="Active Signals"
            icon={<span className="h-2 w-2 rounded-full bg-emerald-500" />}
            iconClassName="bg-emerald-50"
            className="lg:col-span-3"
          >
            {activeSignals.loading ? (
              <LoadingSkeleton />
            ) : activeSignals.error ? (
              <ErrorState message={activeSignals.error} />
            ) : (
              <ActiveSignalsTable signals={activeSignals.data ?? []} />
            )}
          </DataCard>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <DataCard
            title="Strong Signals"
            icon={<Activity size={16} />}
            iconClassName="bg-blue-50 text-blue-600"
            className="lg:col-span-2"
          >
            {strongSignals.loading ? (
              <LoadingSkeleton />
            ) : strongSignals.error ? (
              <ErrorState message={strongSignals.error} />
            ) : (
              <StrongSignalsTable signals={strongSignals.data ?? []} />
            )}
          </DataCard>

          <DataCard
            title="Premium Signals"
            icon={<Gem size={16} />}
            iconClassName="bg-purple-50 text-purple-600"
            className="lg:col-span-3"
          >
            {premiumSignals.loading ? (
              <LoadingSkeleton />
            ) : premiumSignals.error ? (
              <ErrorState message={premiumSignals.error} />
            ) : (
              <PremiumSignalsTable signals={premiumSignals.data ?? []} onView={handleView} />
            )}
          </DataCard>
        </div>
      </main>

      {selectedTradeId ? (
        <SignalDetailsModal
          details={selectedDetails}
          loading={detailsLoading}
          error={detailsError}
          onClose={closeModal}
          onTrade={handleTrade}
          trading={trading}
          tradeError={tradeError}
        />
      ) : null}
    </div>
  );
}
