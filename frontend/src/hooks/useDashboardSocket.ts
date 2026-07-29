import { useEffect, useRef, useState } from "react";
import { DashboardSocketClient, type SocketStatus } from "../services/dashboard.socket";
import type { DashboardWebSocketEvent } from "../types/dashboard";

export function useDashboardSocket(onEvent: (event: DashboardWebSocketEvent) => void) {
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    const client = new DashboardSocketClient(
      (event) => onEventRef.current(event),
      (nextStatus) => setStatus(nextStatus),
    );
    client.connect();
    return () => client.disconnect();
  }, []);

  return { status, isConnected: status === "connected" };
}
