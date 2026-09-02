/**
 * AuditLogPage — displays the append-only audit trail.
 *
 * Uses GET /audit-log with entity_type filter.
 * Each row shows: timestamp, entity type, action, actor, and a JSON diff.
 */
import { useState } from "react";
import { useAuditLog } from "@/api/hooks/useAuditLog";

const ENTITY_TYPES = [
  "",
  "order",
  "invoice",
  "payment",
  "credit_note",
  "customer",
  "product",
  "commission_config",
  "commission_transaction",
  "inventory_transaction",
  "stock_transfer",
  "customer_ledger_entry",
  "report_definition",
  "report_run",
  "kpi_snapshot",
  "approval_request",
  "approval_history",
];

const ACTION_COLORS: Record<string, string> = {
  CREATE: "bg-green-100 text-green-800",
  UPDATE: "bg-blue-100 text-blue-800",
  TRANSITION: "bg-purple-100 text-purple-800",
};

const PAGE_SIZE = 50;

export function AuditLogPage() {
  const [page, setPage] = useState(0);
  const [entityType, setEntityType] = useState("");

  const { data: entries, isLoading } = useAuditLog({
    entity_type: entityType || undefined,
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold text-gray-900">Audit Log</h1>
      <p className="mb-6 text-sm text-gray-500">
        Append-only record of all system mutations. Entries cannot be modified
        or deleted.
      </p>

      {/* Entity type filter */}
      <div className="mb-4">
        <select
          value={entityType}
          onChange={(e) => {
            setEntityType(e.target.value);
            setPage(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All entity types</option>
          {ENTITY_TYPES.filter(Boolean).map((et) => (
            <option key={et} value={et}>
              {et.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : (entries ?? []).length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <p className="text-sm text-gray-500">No audit log entries found.</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Time
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Entity
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Action
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Actor
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Details
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {entries!.map((entry: any) => (
                  <AuditLogRow key={entry.id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Previous
            </button>
            <span className="text-sm text-gray-500">Page {page + 1}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(entries ?? []).length < PAGE_SIZE}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single audit log row with expandable diff
// ---------------------------------------------------------------------------

function AuditLogRow({ entry }: { entry: any }) {
  const [expanded, setExpanded] = useState(false);
  const hasDiff = entry.before_json || entry.after_json;

  return (
    <>
      <tr
        className={`hover:bg-gray-50 ${hasDiff ? "cursor-pointer" : ""}`}
        onClick={() => hasDiff && setExpanded(!expanded)}
      >
        <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
          {new Date(entry.occurred_at).toLocaleString()}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
          <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
            {entry.entity_type?.replace(/_/g, " ")}
          </span>
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-sm">
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
              ACTION_COLORS[entry.action] ?? "bg-gray-100 text-gray-800"
            }`}
          >
            {entry.action}
          </span>
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
          {entry.actor_user_id?.slice(0, 8) ?? "—"}
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
          {hasDiff ? (
            <span className="text-blue-600 hover:underline">
              {expanded ? "▼ Hide" : "▶ View"}
            </span>
          ) : (
            "—"
          )}
        </td>
      </tr>
      {expanded && hasDiff && (
        <tr>
          <td colSpan={5} className="bg-gray-50 px-4 py-3">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {entry.before_json && (
                <div>
                  <h4 className="mb-1 text-xs font-medium uppercase text-gray-500">
                    Before
                  </h4>
                  <pre className="overflow-auto rounded bg-white p-2 text-xs text-gray-700">
                    {JSON.stringify(entry.before_json, null, 2)}
                  </pre>
                </div>
              )}
              {entry.after_json && (
                <div>
                  <h4 className="mb-1 text-xs font-medium uppercase text-gray-500">
                    After
                  </h4>
                  <pre className="overflow-auto rounded bg-white p-2 text-xs text-gray-700">
                    {JSON.stringify(entry.after_json, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
