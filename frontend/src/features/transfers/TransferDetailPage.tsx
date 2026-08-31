import { Link, useParams } from "react-router";
import {
  useTransfer,
  useTransferHistory,
} from "@/api/hooks/useTransfers";
import { useWarehouses } from "@/api/hooks/useWarehouses";
import { ROUTES } from "@/lib/constants";
import { TransferTransitionActions } from "./TransferTransitionActions";

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  PENDING: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  DISPATCHED: "bg-purple-100 text-purple-800",
  RECEIVED: "bg-green-100 text-green-800",
  CANCELLED: "bg-red-100 text-red-800",
};

export function TransferDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: transfer, isLoading, error, refetch } = useTransfer(id ?? "");
  const { data: history } = useTransferHistory(id ?? "");
  const { data: warehouses } = useWarehouses();

  const warehouseMap = Object.fromEntries(
    (warehouses ?? []).map((w: any) => [w.id, w]),
  );

  if (isLoading) return <p className="text-gray-500">Loading…</p>;
  if (error)
    return <p className="text-red-600">Failed to load transfer details.</p>;
  if (!transfer) return <p className="text-gray-500">Transfer not found.</p>;

  const state = transfer.state as string;

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.OFFICE}/transfers`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Back to transfers
        </Link>
      </div>

      {/* Header */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {transfer.transfer_number}
            </h1>
            <div className="mt-2 flex items-center gap-4 text-sm text-gray-500">
              <span>
                Source:{" "}
                <span className="font-medium text-gray-900">
                  {warehouseMap[transfer.source_warehouse_id]?.code ??
                    transfer.source_warehouse_id.slice(0, 8)}
                </span>
              </span>
              <span>→</span>
              <span>
                Destination:{" "}
                <span className="font-medium text-gray-900">
                  {warehouseMap[transfer.destination_warehouse_id]?.code ??
                    transfer.destination_warehouse_id.slice(0, 8)}
                </span>
              </span>
            </div>
          </div>
          <span
            className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${
              STATE_BADGE[state] ?? "bg-gray-100 text-gray-800"
            }`}
          >
            {state}
          </span>
        </div>

        <div className="mt-4 text-sm text-gray-500">
          Requested: {new Date(transfer.requested_at).toLocaleString()}
          {transfer.approved_at && (
            <> · Approved: {new Date(transfer.approved_at).toLocaleString()}</>
          )}
          {transfer.dispatched_at && (
            <>
              {" "}
              · Dispatched:{" "}
              {new Date(transfer.dispatched_at).toLocaleString()}
            </>
          )}
          {transfer.received_at && (
            <>
              {" "}
              · Received:{" "}
              {new Date(transfer.received_at).toLocaleString()}
            </>
          )}
        </div>

        {/* Transition actions */}
        <div className="mt-4">
          <TransferTransitionActions
            transfer={transfer}
            onTransitionComplete={() => refetch()}
          />
        </div>
      </div>

      {/* Lines */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          Transfer Lines
        </h2>
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs font-medium uppercase text-gray-500">
              <th className="pb-2">Product</th>
              <th className="pb-2 text-right">Qty Requested</th>
              <th className="pb-2 text-right">Qty Dispatched</th>
              <th className="pb-2 text-right">Qty Received</th>
              <th className="pb-2 text-right">Unit Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(transfer.lines ?? []).length === 0 ? (
              <tr>
                <td colSpan={5} className="py-4 text-center text-gray-500">
                  No lines.
                </td>
              </tr>
            ) : (
              (transfer.lines ?? []).map((line: any) => (
                <tr key={line.id}>
                  <td className="py-2 font-mono text-xs text-gray-700">
                    {line.product_id.slice(0, 8)}
                  </td>
                  <td className="py-2 text-right text-gray-900">
                    {line.qty_requested}
                  </td>
                  <td className="py-2 text-right text-gray-500">
                    {line.qty_dispatched}
                  </td>
                  <td className="py-2 text-right text-gray-500">
                    {line.qty_received}
                  </td>
                  <td className="py-2 text-right text-gray-900">
                    {Number(line.unit_cost).toLocaleString()}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* History */}
      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          State History
        </h2>
        {(history ?? []).length === 0 ? (
          <p className="text-sm text-gray-500">No history yet.</p>
        ) : (
          <div className="space-y-3">
            {(history ?? []).map((h: any) => (
              <div
                key={h.id}
                className="flex items-center gap-3 text-sm"
              >
                <span className="font-mono text-xs text-gray-500">
                  {new Date(h.event_at).toLocaleString()}
                </span>
                <span className="text-gray-400">→</span>
                <span className="font-medium text-gray-900">
                  {h.from_state} → {h.to_state}
                </span>
                {h.note && (
                  <span className="text-gray-500 italic">— {h.note}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
