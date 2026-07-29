import type { ReactNode } from "react";

interface DataCardProps {
  title: string;
  icon?: ReactNode;
  iconClassName?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function DataCard({ title, icon, iconClassName, action, children, className = "" }: DataCardProps) {
  return (
    <section
      className={`rounded-card border border-slate-200 bg-white p-5 shadow-card ${className}`}
    >
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {icon ? (
            <span
              className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconClassName ?? "bg-slate-100 text-slate-600"}`}
            >
              {icon}
            </span>
          ) : null}
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
