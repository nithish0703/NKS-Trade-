import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ConnectionStatus } from "./ConnectionStatus";

describe("ConnectionStatus", () => {
  it("shows 'Live' and a green dot when connected", () => {
    render(<ConnectionStatus isConnected />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByTestId("connection-dot")).toHaveClass("bg-emerald-500");
  });

  it("shows 'Offline' and a grey dot when disconnected", () => {
    render(<ConnectionStatus isConnected={false} />);
    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(screen.getByTestId("connection-dot")).toHaveClass("bg-slate-300");
  });
});
