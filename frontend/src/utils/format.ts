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

export function formatRelativeTime(value: string | null | undefined, now: Date): string {
  if (!value) {
    return "never";
  }
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) {
    return "never";
  }

  const diffSeconds = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));

  if (diffSeconds < 5) {
    return "just now";
  }
  if (diffSeconds < 60) {
    return `${diffSeconds}s ago`;
  }
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes}m ago`;
  }
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

export function formatUptime(startedAtUtc: string | null | undefined, now: Date): string {
  if (!startedAtUtc) {
    return "—";
  }
  const startedAt = new Date(startedAtUtc);
  if (Number.isNaN(startedAt.getTime())) {
    return "—";
  }

  const totalSeconds = Math.max(0, Math.round((now.getTime() - startedAt.getTime()) / 1000));
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
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
