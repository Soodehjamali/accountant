import { useState } from "react";
import { useParams, Link } from "react-router";
import {
  useOrder,
  useOrderHistory,
  useAddOrderLine,
  useRemoveOrderLine,
  useUpdateOrderLineQty,
  useUpdateOrderLinePrice,
} from "@/api/hooks/useOrders";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";
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

export function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: order, isLoading, error } = useOrder(id ?? "");
  const { data: history } = useOrderHistory(id ?? "");
  const canManage = usePermission(PERMISSIONS.ORDER_MANAGE);

  const isDraft = order?.state === "DRAFT";

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !order) {
    return (
      <div>
        <p className="text-red-600">Order not found.</p>
        <Link
          to={`${ROUTES.OFFICE}/orders`}
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
          to={`${ROUTES.OFFICE}/orders`}
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

      {/* Transition actions */}
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
              Discount Total
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(order.discount_total).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Tax Total</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(order.tax_total).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Grand Total</dt>
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
          {order.shipped_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Shipped At</dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(order.shipped_at).toLocaleString()}
              </dd>
            </div>
          )}
          {order.invoiced_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">
                Invoiced At
              </dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(order.invoiced_at).toLocaleString()}
              </dd>
            </div>
          )}
          {order.paid_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Paid At</dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(order.paid_at).toLocaleString()}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {/* Line items */}
      <div className="mb-6">
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Line Items
        </h2>
        {isDraft && canManage && (
          <AddLineForm orderId={order.id} />
        )}
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
                  Discount
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Line Total
                </th>
                {isDraft && canManage && (
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {order.lines.length === 0 ? (
                <tr>
                  <td
                    colSpan={isDraft && canManage ? 7 : 6}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No line items.
                  </td>
                </tr>
              ) : (
                order.lines.map((line) => (
                  <LineRow
                    key={line.id}
                    line={line}
                    isDraft={isDraft}
                    canManage={canManage}
                    orderId={order.id}
                  />
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
                  Actor
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
                    colSpan={5}
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
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-xs text-gray-500">
                      {entry.actor_user_id.slice(0, 8)}
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

// ---------------------------------------------------------------------------
// Invoice link — fetches the invoice for this order via order_id filter
// ---------------------------------------------------------------------------

function InvoiceLink({ orderId }: { orderId: string }) {
  const { data: invoices } = useInvoices({ order_id: orderId, limit: 1 });
  const invoice = invoices?.[0];

  if (!invoice) return null;

  return (
    <Link
      to={`${ROUTES.OFFICE}/invoices/${invoice.id}`}
      className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
    >
      View Invoice
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Line row with inline editing (DRAFT only)
// ---------------------------------------------------------------------------

function LineRow({
  line,
  isDraft,
  canManage,
  orderId,
}: {
  line: {
    id: string;
    product_id: string;
    qty_ordered: string;
    qty_shipped: string;
    unit_price: string;
    discount_value: string;
    line_total: string;
  };
  isDraft: boolean;
  canManage: boolean;
  orderId: string;
}) {
  const [editing, setEditing] = useState(false);
  const [qty, setQty] = useState(line.qty_ordered);
  const [price, setPrice] = useState(line.unit_price);

  const removeLine = useRemoveOrderLine(orderId);
  const updateQty = useUpdateOrderLineQty(orderId);
  const updatePrice = useUpdateOrderLinePrice(orderId);

  const editable = isDraft && canManage;

  async function handleSave() {
    try {
      if (qty !== line.qty_ordered) {
        await updateQty.mutateAsync({ lineId: line.id, qty_ordered: qty });
      }
      if (price !== line.unit_price) {
        await updatePrice.mutateAsync({ lineId: line.id, unit_price: price });
      }
      setEditing(false);
    } catch {
      // Error will surface via React Query; restore original values on next render
    }
  }

  return (
    <tr className="hover:bg-gray-50">
      <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-xs text-gray-900">
        {line.product_id.slice(0, 8)}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
        {editing ? (
          <input
            type="number"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="w-20 rounded border border-gray-300 px-2 py-1 text-right text-sm"
            min="0"
          />
        ) : (
          <span className="text-gray-900">{line.qty_ordered}</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
        {line.qty_shipped}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
        {editing ? (
          <input
            type="number"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            className="w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm"
            min="0"
            step="0.01"
          />
        ) : (
          <span className="text-gray-900">
            {Number(line.unit_price).toLocaleString()}
          </span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
        {Number(line.discount_value) > 0
          ? Number(line.discount_value).toLocaleString()
          : "—"}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
        {Number(line.line_total).toLocaleString()}
      </td>
      {editable && (
        <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
          {editing ? (
            <span className="space-x-2">
              <button
                onClick={handleSave}
                disabled={updateQty.isPending || updatePrice.isPending}
                className="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50"
              >
                Save
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setQty(line.qty_ordered);
                  setPrice(line.unit_price);
                }}
                className="text-xs text-gray-500 hover:underline"
              >
                Cancel
              </button>
            </span>
          ) : (
            <span className="space-x-2">
              <button
                onClick={() => setEditing(true)}
                className="text-xs text-blue-600 hover:underline"
              >
                Edit
              </button>
              <button
                onClick={() => {
                  if (confirm("Remove this line?")) {
                    removeLine.mutate(line.id);
                  }
                }}
                className="text-xs text-red-600 hover:underline"
              >
                Remove
              </button>
            </span>
          )}
        </td>
      )}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Add line form (DRAFT only)
// ---------------------------------------------------------------------------

function AddLineForm({ orderId }: { orderId: string }) {
  const addLine = useAddOrderLine(orderId);
  const [productId, setProductId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await addLine.mutateAsync({
        product_id: productId,
        fulfillment_warehouse_id: warehouseId,
        qty_ordered: qty,
        fulfillment_mode: "REP_LOCAL",
        ...(price ? { price_history_id: price } : {}),
      });
      setProductId("");
      setWarehouseId("");
      setQty("1");
      setPrice("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add line");
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4"
    >
      <div>
        <label className="block text-xs font-medium text-gray-500">
          Product ID *
        </label>
        <input
          type="text"
          value={productId}
          onChange={(e) => setProductId(e.target.value)}
          required
          placeholder="UUID"
          className="mt-1 block w-64 rounded border border-gray-300 px-2 py-1 text-sm font-mono"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-500">
          Warehouse ID *
        </label>
        <input
          type="text"
          value={warehouseId}
          onChange={(e) => setWarehouseId(e.target.value)}
          required
          placeholder="UUID"
          className="mt-1 block w-64 rounded border border-gray-300 px-2 py-1 text-sm font-mono"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-500">Qty *</label>
        <input
          type="number"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          required
          min="0.01"
          step="any"
          className="mt-1 block w-20 rounded border border-gray-300 px-2 py-1 text-right text-sm"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-500">
          Price History ID
        </label>
        <input
          type="text"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          placeholder="Optional UUID"
          className="mt-1 block w-64 rounded border border-gray-300 px-2 py-1 text-sm font-mono"
        />
      </div>
      <button
        type="submit"
        disabled={addLine.isPending}
        className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {addLine.isPending ? "Adding…" : "Add Line"}
      </button>
      {error && (
        <p className="w-full text-xs text-red-600">{error}</p>
      )}
    </form>
  );
}
