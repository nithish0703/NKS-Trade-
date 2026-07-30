import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SummaryCard } from "./SummaryCard";

describe("SummaryCard", () => {
  it("renders the label and value", () => {
    render(
      <SummaryCard
        label="Total Signals"
        value="324"
        icon={<span />}
        iconClassName="bg-purple-50 text-purple-600"
      />,
    );
    expect(screen.getByText("Total Signals")).toBeInTheDocument();
    expect(screen.getByText("324")).toBeInTheDocument();
  });

  it("renders no change-percentage row when none is provided", () => {
    render(
      <SummaryCard label="Win Rate" value="—" icon={<span />} iconClassName="bg-blue-50 text-blue-600" />,
    );
    expect(screen.queryByText("No data")).not.toBeInTheDocument();
    expect(screen.queryByText(/vs last 30d/)).not.toBeInTheDocument();
  });

  it("renders a positive change percentage", () => {
    render(
      <SummaryCard
        label="Wins"
        value="198"
        icon={<span />}
        iconClassName="bg-emerald-50 text-emerald-600"
        changePercentage={22}
      />,
    );
    expect(screen.getByText(/22%/)).toBeInTheDocument();
  });

  it("renders a negative change percentage", () => {
    render(
      <SummaryCard
        label="Losses"
        value="126"
        icon={<span />}
        iconClassName="bg-red-50 text-red-600"
        changePercentage={-14}
      />,
    );
    expect(screen.getByText(/14%/)).toBeInTheDocument();
  });
});
