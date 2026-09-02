/**
 * ReturnDetailPage — shows a single customer return with lines and actions.
 *
 * Displays return header, lines, and state transition buttons.
 */
import { useState } from "react";
import { useParams, Link } from "react-router";
import { useReturn, useReceiveReturn, useInspectReturn, useCloseReturn } from "@/api/hooks/useReturns";
import { usePermission } from "@/hooks/usePermission";

const STATE_BADGE: Record<string, string> = {
  PENDING_APPROVAL: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  RECEIVED: "bg-purple-100 text-purple-800",
  INSPECTED: "bg-indigo-100 text-indigo-800",
  CLOSED: "bg-green-100 text-green-800",
  REJECTED: "bg-red-100 text-red-800",
};

/** Which transitions are legal from each state. */
const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  PENDING_APPROVAL: [],
  APPROVED: ["receive"],
  RECEIVED: ["inspect"],
  INSPECTED: ["close"],
  CLOSED: [],
  REJECTED: [],
};

export function ReturnDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: ret, isLoading, error } = useReturn(id ?? "");
  const canManage = usePermission("RETURN_MANAGE");

  const receiveReturn = useReceiveReturn();
  const inspectReturn = useInspectReturn();
  const closeReturn = useCloseReturn();

  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleTransition(action: "receive" | "inspect" | "close") {
    setActionError(null);
    try {
      if (action === "receive") {
        await receiveReturn.mutateAsync({ returnId: id!, note: note || null });
      } else if (action === "inspect") {
        await inspectReturn.mutateAsync({ returnId: id!, note: note || null });
      } else {
        await closeReturn.mutateAsync({ returnId: id!, note: note || null });
      }
      setNote("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    }
  }

  if (isLoading) return <p className="text-gray-500">Loading…</p>;
  if (error || !ret) {
    return (
      <div>
        <p className="text-red-600">Return not found.</p>
        <Link to="/office/returns" className="mt-4 inline-block text-sm text-blue-600 hover:underline">
          ← Back to returns
        </Link>
      </div>
    );
  }

  const transitions = ALLOWED_TRANSITIONS[ret.state] ?? [];

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/returns" className="text-sm text-blue-600 hover:underline">
          ← Customer Returns
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Return {ret.return_number}
      </h1>

      {/* Header details */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-gray-500">Type</dt>
            <dd className="mt-1 text-sm text-gray-900">{ret.return_type?.replace(/_/g, " ")}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">State</dt>
            <dd className="mt-1">
              <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[ret.state] ?? "bg-gray-100 text-gray-800"}`}>
                {ret.state?.replace(/_/g, " ")}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Customer</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">{ret.customer_id?.slice(0, 8) ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Order</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">{ret.order_id?.slice(0, 8) ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Warehouse</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">{ret.warehouse_id?.slice(0, 8)}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Requested</dt>
            <dd className="mt-1 text-sm text-gray-600">{new Date(ret.requested_at).toLocaleString()}</dd>
          </div>
          {ret.received_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Received</dt>
              <dd className="mt-1 text-sm text-gray-600">{new Date(ret.received_at).toLocaleString()}</dd>
            </div>
          )}
          {ret.closed_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Closed</dt>
              <dd className="mt-1 text-sm text-gray-600">{new Date(ret.closed_at).toLocaleString()}</dd>
            </div>
          )}
        </dl>
      </div>

      {/* Return lines */}
      <div className="mb-6">
        <h2 className="mb-3 text-lg font-semibold text-gray-900">Lines</h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Product</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">Qty</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">Refund</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Condition</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Disposition</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(ret.lines ?? []).length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-sm text-gray-500">No lines.</td></tr>
              ) : (
                (ret.lines ?? []).map((line: any) => (
                  <tr key={line.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-gray-900">{line.product_id?.slice(0, 8)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">{Number(line.qty_returned)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">{Number(line.unit_refund_amount)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">{line.condition ?? "—"}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">{line.disposition ?? "—"}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Actions */}
      {canManage && transitions.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-3 text-lg font-semibold text-gray-900">Actions</h2>
          <div className="flex items-center gap-3">
            <input
              type="text"
              placeholder="Note (optional)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="w-48 rounded border border-gray-300 px-3 py-2 text-sm"
            />
            {transitions.includes("receive") && (
              <button
                onClick={() => handleTransition("receive")}
                disabled={receiveReturn.isPending}
                className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {receiveReturn.isPending ? "Receiving…" : "Mark Received"}
              </button>
            )}
            {transitions.includes("inspect") && (
              <button
                onClick={() => handleTransition("inspect")}
                disabled={inspectReturn.isPending}
                className="rounded bg-purple-600 px-4 py-2 text-sm text-white hover:bg-purple-700 disabled:opacity-50"
              >
                {inspectReturn.isPending ? "Inspecting…" : "Mark Inspected"}
              </button>
            )}
            {transitions.includes("close") && (
              <button
                onClick={() => handleTransition("close")}
                disabled={closeReturn.isPending}
                className="rounded bg-green-600 px-4 py-2 text-sm text-white hover:bg-green-700 disabled:opacity-50"
              >
                {closeReturn.isPending ? "Closing…" : "Close Return"}
              </button>
            )}
          </div>
          {actionError && (
            <p className="mt-2 text-sm text-red-600">{actionError}</p>
          )}
        </div>
      )}
    </div>
  );
}
