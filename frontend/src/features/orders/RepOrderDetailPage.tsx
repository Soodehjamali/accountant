/**
 * RepOrderDetailPage — order detail view for the Representative Portal.
 *
 * Reuses the full OrderDetailPage component (header, lines, history,
 * transition actions). The only difference is the back link points
 * to /rep/orders instead of /office/orders.
 *
 * Transition actions are gated by the existing usePermission checks
 * — reps with ORDER_MANAGE see the same buttons as office users;
 * reps without it see a read-only view.
 */
import { useParams, Link } from "react-router";
import {
  useOrder,
  useOrderHistory,
} from "@/api/hooks/useOrders";
import { ROUTES } from "@/lib/constants";
import { useInvoices } from "@/api/hooks/useInvoices";
import { OrderTransitionActions } from "./OrderTransitionActions";

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

export function RepOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: order, isLoading, error } = useOrder(id ?? "");
  const { data: history } = useOrderHistory(id ?? "");

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !order) {
    return (
      <div>
        <p className="text-red-600">Order not found.</p>
        <Link
          to={`${ROUTES.REP}/orders`}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to orders
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.REP}/orders`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Orders
        </Link>
      </div>

      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          Order {order.order_number}
        </h1>
        <div className="flex items-center gap-3">
          <span
            className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${
              STATE_BADGE[order.state] ?? "bg-gray-100 text-gray-800"
            }`}
          >
            {order.state.replace(/_/g, " ")}
          </span>
        </div>
      </div>

      {/* Transition actions — same component as office, permission-gated */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <OrderTransitionActions order={order} />
        {/* Invoice link — visible when order is INVOICED or beyond */}
        {["INVOICED", "PAID", "COMPLETED"].includes(order.state) && (
          <InvoiceLink orderId={order.id} />
        )}
      </div>

      {/* Header fields */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-gray-500">Customer</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {order.customer_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Representative
            </dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {order.representative_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Order Type</dt>
            <dd className="mt-1 text-sm text-gray-900">{order.order_type}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Fulfillment Mode
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {order.fulfillment_mode}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Sales Channel
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {order.sales_channel}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Subtotal</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(order.subtotal).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Grand Total
            </dt>
            <dd className="mt-1 text-sm font-bold text-gray-900">
              {Number(order.grand_total).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Ordered At</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(order.ordered_at).toLocaleString()}
            </dd>
          </div>
        </dl>
      </div>

      {/* Line items */}
      <div className="mb-6">
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Line Items
        </h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Product
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Qty Ordered
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Qty Shipped
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Unit Price
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Line Total
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(order.lines ?? []).length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No line items.
                  </td>
                </tr>
              ) : (
                (order.lines ?? []).map((line) => (
                  <tr key={line.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-xs text-gray-900">
                      {line.product_id.slice(0, 8)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                      {line.qty_ordered}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
                      {line.qty_shipped}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                      {Number(line.unit_price).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                      {Number(line.line_total).toLocaleString()}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Status history */}
      <div>
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Status History
        </h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Timestamp
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  From
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  To
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Note
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(!history || history.length === 0) ? (
                <tr>
                  <td
                    colSpan={4}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No history entries.
                  </td>
                </tr>
              ) : (
                history.map((entry) => (
                  <tr key={entry.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                      {new Date(entry.event_at).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {entry.from_state.replace(/_/g, " ")}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {entry.to_state.replace(/_/g, " ")}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">
                      {entry.note ?? "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function InvoiceLink({ orderId }: { orderId: string }) {
  const { data: invoices } = useInvoices({ order_id: orderId, limit: 1 });
  const invoice = invoices?.[0];
  if (!invoice) return null;
  return (
    <Link
      to={`${ROUTES.REP}/orders/${orderId}`}
      className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
    >
      View Invoice
    </Link>
  );
}
