export function formatNumberOrDash(
  value: number | null | undefined,
  options?: Intl.NumberFormatOptions,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", options).format(value);
}

export function formatPercentageOrDash(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}%`;
}

export function formatPriceOrDash(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return formatNumberOrDash(value, { maximumFractionDigits: 8, minimumFractionDigits: 0 });
}

export function formatUtcTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  try {
    const date = new Date(value);
    return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
  } catch {
    return "—";
  }
}

export function formatUtcClock(date: Date): string {
  return `${date.toISOString().slice(11, 19)} UTC`;
}

export function formatIstClock(date: Date): string {
  const time = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  }).format(date);
  return `${time} IST`;
}

export function formatIstTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  try {
    const date = new Date(value);
    const formatted = new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: true,
    }).format(date);
    return `${formatted} IST`;
  } catch {
    return "—";
  }
}
