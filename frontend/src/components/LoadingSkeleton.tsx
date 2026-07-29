interface LoadingSkeletonProps {
  rows?: number;
}

export function LoadingSkeleton({ rows = 4 }: LoadingSkeletonProps) {
  return (
    <div role="status" aria-label="Loading" className="space-y-2">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="h-6 w-full animate-pulse rounded bg-slate-100" />
      ))}
    </div>
  );
}
