import { getWsBaseUrl } from "../utils/env";
import type { DashboardWebSocketEvent } from "../types/dashboard";

export type SocketStatus = "connecting" | "connected" | "disconnected";

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

export class DashboardSocketClient {
  private socket: WebSocket | null = null;
  private reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByCaller = false;

  constructor(
    private readonly onEvent: (event: DashboardWebSocketEvent) => void,
    private readonly onStatusChange: (status: SocketStatus) => void,
  ) {}

  connect(): void {
    this.closedByCaller = false;
    this.openSocket();
  }

  disconnect(): void {
    this.closedByCaller = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.socket?.close();
    this.socket = null;
  }

  private openSocket(): void {
    this.onStatusChange("connecting");
    const socket = new WebSocket(`${getWsBaseUrl()}/ws/dashboard`);
    this.socket = socket;

    socket.onopen = () => {
      this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
      this.onStatusChange("connected");
    };

    socket.onmessage = (messageEvent) => {
      try {
        const parsed = JSON.parse(messageEvent.data) as DashboardWebSocketEvent;
        if (parsed.event === "PING") {
          return;
        }
        this.onEvent(parsed);
      } catch {
        // Ignore malformed frames; the periodic REST reconciliation
        // keeps the dashboard correct even if a message is dropped.
      }
    };

    socket.onclose = () => {
      this.onStatusChange("disconnected");
      this.socket = null;
      if (!this.closedByCaller) {
        this.scheduleReconnect();
      }
    };

    socket.onerror = () => {
      socket.close();
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) {
      return;
    }
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, this.reconnectDelayMs);
    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
  }
}
