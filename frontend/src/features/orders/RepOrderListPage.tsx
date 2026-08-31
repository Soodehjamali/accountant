/**
 * Order list for the Representative Portal.
 *
 * The backend already enforces representative scope (server-side filtering
 * via list_orders's representative_id override).  This page renders
 * order links pointing to /rep/orders/* (not /office/orders/*) and shows
 * the "New Order" button when the user holds ORDER_MANAGE permission.
 *
 * Order creation and transitions are gated by the existing usePermission
 * checks — no rep-specific permission list is hardcoded.
 */
import { useState } from "react";
import { Link } from "react-router";
import { useOrders } from "@/api/hooks/useOrders";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants"

const PAGE_SIZE = 50;

const ORDER_STATES = [
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "RESERVED",
  "FULFILLING",
  "SHIPPED",
  "INVOICED",
  "PAID",
  "COMPLETED",
  "CANCELLED",
  "BACKORDERED",
  "PARTIALLY_FULFILLED",
  "RETURNED",
] as const;

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  PENDING_APPROVAL: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  RESERVED: "bg-indigo-100 text-indigo-800",
  FULFILLING: "bg-purple-100 text-purple-800",
  SHIPPED: "bg-cyan-100 text-cyan-800",
  INVOICED: "bg-teal-100 text-teal-800",
  PAID: "bg-green-100 text-green-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  CANCELLED: "bg-red-100 text-red-800",
  BACKORDERED: "bg-orange-100 text-orange-800",
  PARTIALLY_FULFILLED: "bg-amber-100 text-amber-800",
  RETURNED: "bg-rose-100 text-rose-800",
};

export function RepOrderListPage() {
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");
  const canManage = usePermission(PERMISSIONS.ORDER_MANAGE);

  const { data: orders, isLoading, error } = useOrders({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    state: stateFilter || undefined,
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">My Orders</h1>
        {canManage && (
          <Link
            to={`${ROUTES.REP}/orders/new`}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            New Order
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
          {ORDER_STATES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}
      {error && <p className="text-red-600">Failed to load orders.</p>}

      {!isLoading && !error && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Order #
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Customer
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Type
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    State
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    Grand Total
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Ordered
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(orders ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-8 text-center text-sm text-gray-500"
                    >
                      No orders found.
                    </td>
                  </tr>
                ) : (
                  (orders ?? []).map((order) => (
                    <tr key={order.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <Link
                          to={`${ROUTES.REP}/orders/${order.id}`}
                          className="font-medium text-blue-600 hover:underline"
                        >
                          {order.order_number}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        <span className="font-mono text-xs text-gray-500">
                          {order.customer_id.slice(0, 8)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {order.order_type}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            STATE_BADGE[order.state] ?? "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {order.state.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                        {Number(order.grand_total).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {new Date(order.ordered_at).toLocaleDateString()}
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
              disabled={(orders ?? []).length < PAGE_SIZE}
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
