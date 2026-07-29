import { apiGet } from "../api/client";
import type { SignalDetails } from "../types/dashboard";

export const signalsApi = {
  getSignalDetails: (tradeId: string): Promise<SignalDetails> =>
    apiGet<SignalDetails>(`/api/signals/${encodeURIComponent(tradeId)}`),
};
