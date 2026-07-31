interface EmptyStateProps {
  message?: string;
}

export function EmptyState({ message = "No data" }: EmptyStateProps) {
  return <div className="py-4 text-center text-sm text-slate-400">{message}</div>;
}
