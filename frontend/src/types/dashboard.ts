export type Direction = "BUY" | "SELL";

export type ScanningCoinStatus = "READY" | "SCANNING" | "REJECTED" | "ERROR" | "DUPLICATE";

export interface ComparisonPercentages {
  total_signals_percentage: number | null;
  wins_percentage: number | null;
  losses_percentage: number | null;
  win_rate_percentage: number | null;
  average_rr_percentage: number | null;
}

export interface DashboardSummary {
  total_signals: number;
  wins: number;
  losses: number;
  open_signals: number;
  win_rate: number | null;
  average_rr: number | null;
  confirmed_count: number;
  scanner_running: boolean;
  last_scan_time_utc: string | null;
  server_time_utc: string;
  comparison: ComparisonPercentages;
}

export interface ScanningCoin {
  coin: string;
  // Dashboard-only "live price" display (latest exchange traded
  // price). Independent of scan status; never used for any trade
  // decision. Null if the bulk ticker fetch failed for this symbol.
  price: number | null;
  direction: Direction | null;
  // Ranking-only score: how many pipeline stages this coin cleared so
  // far this cycle. Used only to order the Scanning Coins table; never
  // used to generate a signal (that decision is purely binary
  // CONFIRMED/REJECTED).
  score: number | null;
  status: ScanningCoinStatus;
  failed_layer: string | null;
  reason: string | null;
  updated_at_utc: string | null;
  // Dashboard-only scan-progress visibility (a stage-pass count, never
  // a confidence score; never used to publish/store/notify a signal).
  validation_progress_raw_score: number | null;
  validation_progress_max_score: number | null;
  validation_progress_percentage: number | null;
  last_executed_layer: string | null;
  // Stage 5 (Volume Profile + CVD) confidence tier ("HIGH"/"MEDIUM"/
  // "LOW") and its reasoning. A soft confidence signal only -- never a
  // gate, so `failed_layer` can never be "ORDER_FLOW". Null until
  // Stage 5 has run (i.e. once HTF Bias, Liquidity Sweep, BOS, and
  // IFVG have all passed).
  order_flow_confidence: string | null;
  order_flow_reason: string | null;
}

export interface ActiveSignal {
  trade_id: string;
  coin: string;
  direction: Direction;
  current_price: number | null;
  entry_price: number;
  take_profit: number;
  stop_loss: number;
  distance_to_take_profit_percentage: number | null;
  // Binary signal status (CONFIRMED/REJECTED). Present only for Premium
  // access tier callers; null for Free. No score, percentage, or
  // confidence value is ever included anywhere on this model.
  status: string | null;
  detection_time_utc: string;
  // Dashboard-only lifecycle status; always "ACTIVE" here. Never a real
  // exchange position.
  dashboard_status: "ACTIVE";
}

export interface PremiumSignal {
  trade_id: string;
  coin: string;
  direction: Direction;
  // Binary signal status (CONFIRMED/REJECTED). Present only for Premium
  // access tier callers; null for Free.
  status: string | null;
  entry_price: number;
  take_profit: number;
  stop_loss: number;
  detection_time_utc: string;
}

export interface RejectionItem {
  coin: string;
  failed_layer: string;
  reason: string;
  detection_time_utc: string;
}

export interface ScannerStatusItem {
  pair: string;
  stage: string | null;
  status: ScanningCoinStatus;
  last_scan_time_utc: string | null;
}

export interface ScannerStatusResponse {
  running: boolean;
  shutdown_requested: boolean;
  cycles_completed: number;
  last_cycle_started_at: string | null;
  last_cycle_completed_at: string | null;
  last_cycle_id: string | null;
  last_error: string | null;
}

export interface DashboardHealth {
  scanner_running: boolean;
  database_reachable: boolean;
  telegram_enabled: boolean;
  websocket_enabled: boolean;
  server_time_utc: string;
  started_at_utc: string;
}

export interface SignalDetails {
  trade_id: string;
  coin: string;
  direction: Direction;
  // Binary signal status (CONFIRMED/REJECTED). Present only for Premium
  // access tier callers; null for Free.
  status: string | null;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  risk_reward_ratio: number;
  liquidity_type: string;
  entry_zone_type: string;
  structure_confirmation: string;
  detection_time_utc: string;
  institutional_reason: string;
  // Dashboard-only lifecycle status ("NEW" or "ACTIVE"). Drives the
  // modal's Trade button; never a real trading field.
  dashboard_status: "NEW" | "ACTIVE";
}

export type DashboardWebSocketEventType =
  | "SCAN_CYCLE_STARTED"
  | "PAIR_SCAN_UPDATED"
  | "SCAN_CYCLE_COMPLETED"
  | "SIGNAL_STORED"
  | "SIGNAL_DUPLICATE"
  | "TELEGRAM_SENT"
  | "TELEGRAM_FAILED"
  | "SCANNER_STATUS_CHANGED"
  | "HEARTBEAT"
  | "PING";

export interface DashboardWebSocketEvent {
  event: DashboardWebSocketEventType;
  timestamp_utc: string;
  data: Record<string, unknown>;
}

export interface ApiError {
  detail: string;
}
