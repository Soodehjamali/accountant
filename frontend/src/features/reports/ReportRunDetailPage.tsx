import { useParams, Link } from "react-router";
import { useReportRun } from "@/api/hooks/useReports";

export function ReportRunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useReportRun(id ?? null);

  if (isLoading) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Report Run</h1>
        <p className="mt-4 text-gray-500">Loading…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Report Run</h1>
        <p className="mt-4 text-red-600">Failed to load report run.</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Report Run</h1>
        <p className="mt-4 text-gray-500">Run not found.</p>
      </div>
    );
  }

  const { run, snapshot } = data;
  const rows = (snapshot?.snapshot_data as any)?.rows ?? [];
  const reportType = (snapshot?.snapshot_data as any)?.report_type ?? "—";
  const reportName = (snapshot?.snapshot_data as any)?.report_name ?? "—";

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/reports"
          className="text-sm text-blue-600 hover:underline"
        >
          ← Back to Reports
        </Link>
      </div>

      <h1 className="text-2xl font-bold text-gray-900">Report Run</h1>

      {/* Run metadata */}
      <div className="mt-4 rounded-lg border border-gray-200 bg-white p-6">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Status
            </dt>
            <dd className="mt-1">
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                  run.status === "COMPLETE"
                    ? "bg-green-100 text-green-800"
                    : run.status === "FAILED"
                      ? "bg-red-100 text-red-800"
                      : "bg-yellow-100 text-yellow-800"
                }`}
              >
                {run.status}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Report Type
            </dt>
            <dd className="mt-1 font-mono text-sm text-gray-900">
              {reportType}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Report Name
            </dt>
            <dd className="mt-1 text-sm text-gray-900">{reportName}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Rows
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {run.row_count ?? snapshot?.row_count ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Started
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {run.started_at
                ? new Date(run.started_at).toLocaleString()
                : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase text-gray-500">
              Completed
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {run.completed_at
                ? new Date(run.completed_at).toLocaleString()
                : "—"}
            </dd>
          </div>
        </div>
      </div>

      {/* Snapshot data table */}
      {rows.length > 0 && (
        <div className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            Report Data
          </h2>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs font-medium uppercase text-gray-500">
                  {Object.keys(rows[0]).map((key) => (
                    <th key={key} className="pb-2 pe-4">
                      {key.replace(/_/g, " ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((row: Record<string, unknown>, idx: number) => (
                  <tr key={idx} className="hover:bg-gray-50">
                    {Object.values(row).map((val, colIdx) => (
                      <td
                        key={colIdx}
                        className="whitespace-nowrap py-2 pe-4 text-sm text-gray-900"
                      >
                        {typeof val === "number"
                          ? val.toLocaleString()
                          : String(val ?? "—")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!snapshot && run.status === "COMPLETE" && (
        <p className="mt-4 text-sm text-gray-500">
          No snapshot data available.
        </p>
      )}

      {run.status === "RUNNING" && (
        <p className="mt-4 text-sm text-yellow-600">
          This report is still running. Refresh to check status.
        </p>
      )}
    </div>
  );
}
