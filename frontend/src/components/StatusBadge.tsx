interface StatusBadgeProps {
  status: "READY" | "SCANNING" | "REJECTED" | "ERROR" | "DUPLICATE";
}

const STYLES: Record<StatusBadgeProps["status"], string> = {
  READY: "bg-emerald-50 text-emerald-700",
  SCANNING: "bg-blue-50 text-blue-700",
  REJECTED: "bg-orange-50 text-orange-700",
  ERROR: "bg-red-50 text-red-700",
  DUPLICATE: "bg-slate-100 text-slate-600",
};

const LABELS: Record<StatusBadgeProps["status"], string> = {
  READY: "Ready",
  SCANNING: "Scanning",
  REJECTED: "Rejected",
  ERROR: "Error",
  DUPLICATE: "Duplicate",
};

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${STYLES[status]}`}
    >
      {LABELS[status]}
    </span>
  );
}
