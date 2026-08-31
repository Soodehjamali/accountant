import { useState } from "react";
import { useWarehouses } from "@/api/hooks/useWarehouses";
import { useProducts } from "@/api/hooks/useProducts";
import {
  useInventoryTransactions,
  useInventoryBalance,
  usePostTransaction,
  useReverseTransaction,
} from "@/api/hooks/useInventory";
import { useReasonCodes } from "@/api/hooks/useReasonCodes";
import { useMovementTypes } from "@/api/hooks/useInventory";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function InventoryLedgerPage() {
  const canManage = usePermission(PERMISSIONS.INVENTORY_MANAGE);

  const { data: warehouses } = useWarehouses();
  const { data: products } = useProducts();

  const [warehouseId, setWarehouseId] = useState("");
  const [productId, setProductId] = useState("");

  const { data: balance } = useInventoryBalance(
    warehouseId,
    productId,
  );

  const { data: transactions, isLoading } = useInventoryTransactions({
    warehouse_id: warehouseId,
    product_id: productId || undefined,
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Inventory Ledger
      </h1>

      <p className="mb-4 text-sm text-gray-500">
        Balances are always computed live from the immutable ledger — they
        update whenever transactions change.
      </p>

      {/* Warehouse + Product selectors */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Warehouse *
            </label>
            <select
              value={warehouseId}
              onChange={(e) => setWarehouseId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select warehouse…</option>
              {(warehouses ?? []).map((wh: any) => (
                <option key={wh.id} value={wh.id}>
                  {wh.code} — {wh.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Product *
            </label>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select product…</option>
              {(products ?? []).map((p: any) => (
                <option key={p.id} value={p.id}>
                  {p.sku} — {p.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Balance widget */}
      {warehouseId && productId && (
        <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="text-lg font-semibold text-gray-900">
            Current Balance
          </h2>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {balance != null ? Number(balance.balance).toLocaleString() : "—"}
          </p>
          <p className="mt-1 text-sm text-gray-500">
            Computed live from the ledger (not cached)
          </p>
        </div>
      )}

      {/* Ledger table */}
      {warehouseId && (
        <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            Transactions
          </h2>
          {isLoading ? (
            <p className="text-gray-500">Loading…</p>
          ) : (transactions ?? []).length === 0 ? (
            <p className="text-sm text-gray-500">
              No transactions for this warehouse{productId ? " and product" : ""}.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs font-medium uppercase text-gray-500">
                    <th className="pb-2">Seq #</th>
                    <th className="pb-2">Movement Type</th>
                    <th className="pb-2 text-right">Signed Qty</th>
                    <th className="pb-2 text-right">Unit Cost</th>
                    <th className="pb-2">Reference</th>
                    <th className="pb-2">Reversed</th>
                    <th className="pb-2">Date</th>
                    {canManage && <th className="pb-2">Action</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {(transactions ?? []).map((txn: any) => (
                    <TransactionRow
                      key={txn.id}
                      txn={txn}
                      canManage={canManage}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Post Transaction form */}
      {canManage && warehouseId && productId && (
        <PostTransactionSection
          warehouseId={warehouseId}
          productId={productId}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Transaction row with optional reverse action
// ---------------------------------------------------------------------------

function TransactionRow({
  txn,
  canManage,
}: {
  txn: any;
  canManage: boolean;
}) {
  const [showReverseDialog, setShowReverseDialog] = useState(false);
  const reverseTransaction = useReverseTransaction();
  const { data: adjustmentCodes } = useReasonCodes("ADJUSTMENT");
  const [reasonCodeId, setReasonCodeId] = useState("");

  async function handleReverse() {
    try {
      await reverseTransaction.mutateAsync({
        transactionId: txn.id,
        reason_code_id: reasonCodeId || null,
      });
      setShowReverseDialog(false);
      setReasonCodeId("");
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <>
      <tr className={txn.is_reversed ? "opacity-50" : ""}>
        <td className="py-2 font-mono text-xs text-gray-700">
          {txn.sequence_no}
        </td>
        <td className="py-2 text-gray-900">{txn.movement_type_id?.slice(0, 8)}</td>
        <td
          className={`py-2 text-right font-medium ${
            Number(txn.signed_quantity) >= 0
              ? "text-green-700"
              : "text-red-700"
          }`}
        >
          {Number(txn.signed_quantity) > 0 ? "+" : ""}
          {Number(txn.signed_quantity).toLocaleString()}
        </td>
        <td className="py-2 text-right text-gray-900">
          {Number(txn.unit_cost).toLocaleString()}
        </td>
        <td className="py-2 text-xs text-gray-500">
          {txn.reference_type && (
            <span>
              {txn.reference_type}/{txn.reference_id?.slice(0, 8)}
            </span>
          )}
        </td>
        <td className="py-2 text-center">
          {txn.is_reversed && (
            <span className="text-red-500 text-xs">✓ Reversed</span>
          )}
        </td>
        <td className="py-2 text-sm text-gray-500">
          {new Date(txn.occurred_at).toLocaleDateString()}
        </td>
        {canManage && (
          <td className="py-2">
            {!txn.is_reversed && (
              <button
                onClick={() => setShowReverseDialog(true)}
                className="text-xs text-red-600 hover:underline"
              >
                Reverse
              </button>
            )}
          </td>
        )}
      </tr>

      {showReverseDialog && (
        <tr>
          <td colSpan={8} className="bg-gray-50 p-3">
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-700">Reason:</span>
              <select
                value={reasonCodeId}
                onChange={(e) => setReasonCodeId(e.target.value)}
                className="rounded border border-gray-300 px-2 py-1 text-sm"
              >
                <option value="">Select reason (optional)…</option>
                {(adjustmentCodes ?? []).map((rc: any) => (
                  <option key={rc.id} value={rc.id}>
                    {rc.code} — {rc.label}
                  </option>
                ))}
              </select>
              <button
                onClick={handleReverse}
                disabled={reverseTransaction.isPending}
                className="rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700 disabled:opacity-50"
              >
                {reverseTransaction.isPending ? "Reversing…" : "Confirm Reverse"}
              </button>
              <button
                onClick={() => setShowReverseDialog(false)}
                className="text-sm text-gray-500 hover:underline"
              >
                Cancel
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Post Transaction form
// ---------------------------------------------------------------------------

function PostTransactionSection({
  warehouseId,
  productId,
}: {
  warehouseId: string;
  productId: string;
}) {
  const { data: movementTypes } = useMovementTypes();
  const postTransaction = usePostTransaction();
  const [movementTypeCode, setMovementTypeCode] = useState("");
  const [signedQuantity, setSignedQuantity] = useState("");
  const [unitCost, setUnitCost] = useState("0");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const selectedType = (movementTypes ?? []).find(
    (mt: any) => mt.code === movementTypeCode,
  );
  const expectedSign = selectedType?.sign;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (!movementTypeCode) {
      setError("Movement type is required.");
      return;
    }
    if (!signedQuantity || Number(signedQuantity) === 0) {
      setError("Signed quantity must be non-zero.");
      return;
    }

    // Client-side sign validation (UX convenience)
    if (expectedSign != null) {
      const actualSign = Number(signedQuantity) > 0 ? 1 : -1;
      if (actualSign !== expectedSign) {
        setError(
          `Sign mismatch: ${movementTypeCode} requires a ${
            expectedSign > 0 ? "positive" : "negative"
          } quantity.`,
        );
        return;
      }
    }

    try {
      await postTransaction.mutateAsync({
        product_id: productId,
        warehouse_id: warehouseId,
        movement_type_code: movementTypeCode,
        signed_quantity: signedQuantity,
        unit_cost: unitCost || "0",
        currency_id: "00000000-0000-0000-0000-000000000000", // placeholder
      });
      setSuccess(true);
      setSignedQuantity("");
      setUnitCost("0");
      setMovementTypeCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to post transaction");
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6">
      <h2 className="mb-4 text-lg font-semibold text-gray-900">
        Post Transaction
      </h2>
      <p className="mb-4 text-sm text-gray-500">
        Post a new ledger entry. The signed quantity must match the movement
        type's sign convention (shown in parentheses).
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Movement Type *
            </label>
            <select
              value={movementTypeCode}
              onChange={(e) => setMovementTypeCode(e.target.value)}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Select type…</option>
              {(movementTypes ?? []).map((mt: any) => (
                <option key={mt.code} value={mt.code}>
                  {mt.label} ({mt.sign > 0 ? "+" : ""}{mt.sign})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Signed Quantity *
            </label>
            <input
              type="number"
              step="any"
              value={signedQuantity}
              onChange={(e) => setSignedQuantity(e.target.value)}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder={expectedSign != null ? (expectedSign > 0 ? "e.g. 100" : "e.g. -10") : ""}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Unit Cost
            </label>
            <input
              type="number"
              step="any"
              min="0"
              value={unitCost}
              onChange={(e) => setUnitCost(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {success && (
          <div className="rounded-md bg-green-50 p-3 text-sm text-green-700">
            Transaction posted successfully.
          </div>
        )}

        <button
          type="submit"
          disabled={postTransaction.isPending}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {postTransaction.isPending ? "Posting…" : "Post Transaction"}
        </button>
      </form>
    </div>
  );
}
