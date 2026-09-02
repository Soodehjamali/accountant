/**
 * ReturnListPage — lists all customer returns with state filter.
 *
 * Replaces the concept of a placeholder at /office/returns.
 */
import { useState } from "react";
import { Link } from "react-router";
import { useReturnsList } from "@/api/hooks/useReturns";

const STATE_BADGE: Record<string, string> = {
  PENDING_APPROVAL: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  RECEIVED: "bg-purple-100 text-purple-800",
  INSPECTED: "bg-indigo-100 text-indigo-800",
  CLOSED: "bg-green-100 text-green-800",
  REJECTED: "bg-red-100 text-red-800",
};

const PAGE_SIZE = 25;

export function ReturnListPage() {
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");

  const { data: returns, isLoading } = useReturnsList({
    state: stateFilter || undefined,
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Customer Returns</h1>
        <Link
          to="/office/returns/new"
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          New Return
        </Link>
      </div>

      {/* State filter */}
      <div className="mb-4">
        <select
          value={stateFilter}
          onChange={(e) => {
            setStateFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All states</option>
          <option value="PENDING_APPROVAL">Pending Approval</option>
          <option value="APPROVED">Approved</option>
          <option value="RECEIVED">Received</option>
          <option value="INSPECTED">Inspected</option>
          <option value="CLOSED">Closed</option>
          <option value="REJECTED">Rejected</option>
        </select>
      </div>

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : (returns ?? []).length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <p className="text-sm text-gray-500">No customer returns found.</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Return #
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Type
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    State
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Customer
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Order
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Requested
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {returns!.map((ret: any) => (
                  <tr key={ret.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <Link
                        to={`/office/returns/${ret.id}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {ret.return_number}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {ret.return_type.replace(/_/g, " ")}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          STATE_BADGE[ret.state] ?? "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {ret.state.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {ret.customer_id?.slice(0, 8) ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {ret.order_id ? ret.order_id.slice(0, 8) + "…" : "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {new Date(ret.requested_at).toLocaleDateString()}
                    </td>
                  </tr>
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
              disabled={(returns ?? []).length < PAGE_SIZE}
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
