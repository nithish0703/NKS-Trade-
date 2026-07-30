import { apiGet, apiPost } from "../api/client";
import type {
  ActiveSignal,
  DashboardHealth,
  DashboardSummary,
  PremiumSignal,
  RejectionItem,
  ScannerStatusResponse,
  ScanningCoin,
  StrongSignal,
} from "../types/dashboard";

export const dashboardApi = {
  getSummary: (): Promise<DashboardSummary> => apiGet<DashboardSummary>("/api/dashboard/summary"),

  getScanningCoins: (): Promise<ScanningCoin[]> =>
    apiGet<ScanningCoin[]>("/api/dashboard/scanning-coins"),

  getActiveSignals: (): Promise<ActiveSignal[]> =>
    apiGet<ActiveSignal[]>("/api/dashboard/active-signals"),

  activateSignal: (tradeId: string): Promise<ActiveSignal> =>
    apiPost<ActiveSignal>(`/api/dashboard/signals/${encodeURIComponent(tradeId)}/activate`),

  getPremiumSignals: (): Promise<PremiumSignal[]> =>
    apiGet<PremiumSignal[]>("/api/dashboard/premium-signals"),

  getStrongSignals: (): Promise<StrongSignal[]> =>
    apiGet<StrongSignal[]>("/api/dashboard/strong-signals"),

  getRecentRejections: (): Promise<RejectionItem[]> =>
    apiGet<RejectionItem[]>("/api/dashboard/recent-rejections"),

  getHealth: (): Promise<DashboardHealth> => apiGet<DashboardHealth>("/api/dashboard/health"),

  getScannerStatus: (): Promise<ScannerStatusResponse> =>
    apiGet<ScannerStatusResponse>("/api/scanner/status"),
};
