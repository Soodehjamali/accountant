import { useState } from "react";
import { Link } from "react-router";
import { useTransfers } from "@/api/hooks/useTransfers";
import { useWarehouses } from "@/api/hooks/useWarehouses";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";

const PAGE_SIZE = 50;

const TRANSFER_STATES = [
  "DRAFT",
  "PENDING",
  "APPROVED",
  "DISPATCHED",
  "RECEIVED",
  "CANCELLED",
] as const;

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  PENDING: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  DISPATCHED: "bg-purple-100 text-purple-800",
  RECEIVED: "bg-green-100 text-green-800",
  CANCELLED: "bg-red-100 text-red-800",
};

export function TransferListPage() {
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");
  const canManage = usePermission(PERMISSIONS.TRANSFER_MANAGE);

  const { data: warehouses } = useWarehouses();
  const warehouseMap = Object.fromEntries(
    (warehouses ?? []).map((w: any) => [w.id, w]),
  );

  const { data: transfers, isLoading, error } = useTransfers({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    state: stateFilter || undefined,
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Stock Transfers</h1>
        {canManage && (
          <Link
            to={`${ROUTES.OFFICE}/transfers/new`}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            New Transfer
          </Link>
        )}
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <select
          value={stateFilter}
          onChange={(e) => {
            setStateFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All states</option>
          {TRANSFER_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}
      {error && <p className="text-red-600">Failed to load transfers.</p>}

      {!isLoading && !error && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Transfer #
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Source
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Destination
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Lines
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    State
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Requested
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(transfers ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-8 text-center text-sm text-gray-500"
                    >
                      No stock transfers found.
                    </td>
                  </tr>
                ) : (
                  (transfers ?? []).map((transfer: any) => (
                    <tr key={transfer.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <Link
                          to={`${ROUTES.OFFICE}/transfers/${transfer.id}`}
                          className="font-medium text-blue-600 hover:underline"
                        >
                          {transfer.transfer_number}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {warehouseMap[transfer.source_warehouse_id]?.code ??
                          transfer.source_warehouse_id.slice(0, 8)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {warehouseMap[transfer.destination_warehouse_id]
                          ?.code ?? transfer.destination_warehouse_id.slice(0, 8)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {(transfer.lines ?? []).length}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            STATE_BADGE[transfer.state] ??
                            "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {transfer.state}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {new Date(transfer.requested_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))
                )}
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
              disabled={(transfers ?? []).length < PAGE_SIZE}
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
