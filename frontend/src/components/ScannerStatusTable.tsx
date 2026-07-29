import type { RejectionItem } from "../types/dashboard";
import { EmptyState } from "./EmptyState";
import { formatUtcTime } from "../utils/format";

interface ScannerStatusTableProps {
  rejections: RejectionItem[];
}

export function ScannerStatusTable({ rejections }: ScannerStatusTableProps) {
  if (rejections.length === 0) {
    return <EmptyState message="No recent rejections" />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-slate-400">
            <th className="pb-2 font-medium">Coin</th>
            <th className="pb-2 font-medium">Failed Layer</th>
            <th className="pb-2 font-medium">Reason</th>
            <th className="pb-2 font-medium">Detected</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rejections.map((item, index) => (
            <tr key={`${item.coin}-${index}`}>
              <td className="py-2.5 font-medium text-slate-900">{item.coin}</td>
              <td className="py-2.5">
                <span className="inline-flex items-center rounded-md bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-700">
                  {item.failed_layer}
                </span>
              </td>
              <td className="max-w-xs truncate py-2.5 text-slate-500" title={item.reason}>
                {item.reason}
              </td>
              <td className="py-2.5 text-xs text-slate-400">{formatUtcTime(item.detection_time_utc)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
