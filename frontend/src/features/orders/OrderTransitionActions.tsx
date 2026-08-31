/**
 * OrderTransitionActions — renders legal next action buttons for an order
 * based on its current state, per ADR-004's accepted graph.
 *
 * The ALLOWED_TRANSITIONS table is the single source of truth for which
 * transitions are legal from each state.  Each action button is gated
 * behind the relevant permission code (confirmed against orders.py's
 * _require_order_manage / _require_order_approve).
 *
 * Ship and Pay have dedicated dialogs because their request bodies are
 * NOT generic OrderTransitionRequest — Ship requires ShipOrderRequest
 * (line-by-line quantities) and Pay requires OrderPaymentRequest
 * (amount, method, reference, note).
 */

import { useState } from "react";
import type { components } from "@/api/types";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";
import {
  useSubmitOrder,
  useApproveOrder,
  useReserveOrder,
  useResubmitOrder,
  useCancelOrder,
  useStartFulfillment,
  useRecordReturn,
  useMarkInvoiced,
  useMarkCompleted,
  useShipOrder,
  useMarkPaid,
} from "@/api/hooks/useOrders";

type OrderState = components["schemas"]["OrderState"];
type OrderResponse = components["schemas"]["OrderResponse"];

/**
 * ADR-004 accepted graph — keys are the current state, values are the set
 * of directly reachable states.  Derived from services/order_service.py's
 * ALLOWED_TRANSITIONS (the canonical backend source).
 */
export const ALLOWED_TRANSITIONS: Record<OrderState, OrderState[]> = {
  DRAFT: ["PENDING_APPROVAL", "CANCELLED"],
  PENDING_APPROVAL: ["APPROVED", "CANCELLED"],
  APPROVED: ["RESERVED", "BACKORDERED", "CANCELLED"],
  RESERVED: ["FULFILLING", "CANCELLED"],
  BACKORDERED: ["PENDING_APPROVAL", "CANCELLED"],
  FULFILLING: ["SHIPPED", "PARTIALLY_FULFILLED", "CANCELLED"],
  PARTIALLY_FULFILLED: ["SHIPPED", "RETURNED", "CANCELLED"],
  SHIPPED: ["INVOICED", "RETURNED"],
  INVOICED: ["PAID"],
  PAID: ["COMPLETED"],
  COMPLETED: [],
  CANCELLED: [],
  RETURNED: [],
};

/**
 * Human-readable labels for each transition action.
 */
const ACTION_LABELS: Record<string, string> = {
  PENDING_APPROVAL: "Submit",
  APPROVED: "Approve",
  RESERVED: "Reserve Stock",
  BACKORDERED: "Backorder",
  CANCELLED: "Cancel",
  FULFILLING: "Start Fulfillment",
  SHIPPED: "Ship",
  PARTIALLY_FULFILLED: "Partial Ship",
  INVOICED: "Mark Invoiced",
  PAID: "Mark Paid",
  COMPLETED: "Mark Completed",
  RETURNED: "Return",
  PENDING_APPROVAL_resubmit: "Resubmit",
};

/**
 * Permission required for each target state.
 * APPROVED requires ORDER_APPROVE; everything else requires ORDER_MANAGE.
 * (Confirmed against orders.py's _require_order_approve and
 *  _require_order_manage dependencies.)
 */
const TRANSITION_PERMISSION: Record<string, string> = {
  APPROVED: PERMISSIONS.ORDER_APPROVE,
};

interface OrderTransitionActionsProps {
  order: OrderResponse;
  onTransitionComplete?: () => void;
}

export function OrderTransitionActions({
  order,
  onTransitionComplete,
}: OrderTransitionActionsProps) {
  const state = order.state as OrderState;
  const allowed = ALLOWED_TRANSITIONS[state] ?? [];
  const canManage = usePermission(PERMISSIONS.ORDER_MANAGE);

  if (allowed.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {allowed.map((targetState) => (
        <TransitionButton
          key={targetState}
          order={order}
          targetState={targetState}
          canManage={canManage}
          onTransitionComplete={onTransitionComplete}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual transition button — dispatches to the right dialog/component
// ---------------------------------------------------------------------------

function TransitionButton({
  order,
  targetState,
  canManage,
  onTransitionComplete,
}: {
  order: OrderResponse;
  targetState: OrderState;
  canManage: boolean;
  onTransitionComplete?: () => void;
}) {
  const permissionCode =
    TRANSITION_PERMISSION[targetState] ?? PERMISSIONS.ORDER_MANAGE;
  const hasPermission = usePermission(permissionCode);

  const isResubmit =
    order.state === "BACKORDERED" && targetState === "PENDING_APPROVAL";
  const label = isResubmit
    ? ACTION_LABELS["PENDING_APPROVAL_resubmit"]
    : ACTION_LABELS[targetState] ?? targetState.replace(/_/g, " ");

  const isDestructive = targetState === "CANCELLED" || targetState === "RETURNED";

  if (!hasPermission || !canManage) return null;

  // Ship: needs ShipOrderRequest dialog (line-by-line qty entry)
  if (targetState === "SHIPPED" || targetState === "PARTIALLY_FULFILLED") {
    return (
      <ShipDialog
        order={order}
        label={label}
        onTransitionComplete={onTransitionComplete}
      />
    );
  }

  // Pay: needs OrderPaymentRequest dialog (amount, method, reference, note)
  if (targetState === "PAID") {
    return (
      <PayDialog
        order={order}
        label={label}
        onTransitionComplete={onTransitionComplete}
      />
    );
  }

  // Simple transitions (submit/approve/reserve/resubmit/start-fulfillment/
  // mark-invoiced/mark-completed)
  if (
    (targetState === "PENDING_APPROVAL" && order.state === "DRAFT") ||
    targetState === "APPROVED" ||
    targetState === "RESERVED" ||
    targetState === "FULFILLING" ||
    targetState === "INVOICED" ||
    targetState === "COMPLETED"
  ) {
    return (
      <SimpleTransitionButton
        order={order}
        targetState={targetState}
        label={label}
        onTransitionComplete={onTransitionComplete}
      />
    );
  }

  // Destructive transitions need confirmation
  if (isDestructive) {
    return (
      <ConfirmTransitionButton
        order={order}
        targetState={targetState}
        label={label}
        onTransitionComplete={onTransitionComplete}
      />
    );
  }

  // Remaining (resubmit from backorder, etc.)
  return (
    <SimpleTransitionButton
      order={order}
      targetState={targetState}
      label={label}
      onTransitionComplete={onTransitionComplete}
    />
  );
}

// ---------------------------------------------------------------------------
// Simple transition (submit, approve, reserve, start-fulfillment,
// mark-invoiced, mark-completed, resubmit)
// ---------------------------------------------------------------------------

function SimpleTransitionButton({
  order,
  targetState,
  label,
  onTransitionComplete,
}: {
  order: OrderResponse;
  targetState: OrderState;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const submitOrder = useSubmitOrder();
  const approveOrder = useApproveOrder();
  const reserveOrder = useReserveOrder();
  const resubmitOrder = useResubmitOrder();
  const startFulfillment = useStartFulfillment();
  const markInvoiced = useMarkInvoiced();
  const markCompleted = useMarkCompleted();

  const mutations: Record<
    string,
    { mutateAsync: (params: any) => Promise<any>; isPending: boolean }
  > = {
    PENDING_APPROVAL: submitOrder,
    APPROVED: approveOrder,
    RESERVED: reserveOrder,
    FULFILLING: startFulfillment,
    INVOICED: markInvoiced,
    COMPLETED: markCompleted,
  };

  const isResubmit =
    order.state === "BACKORDERED" && targetState === "PENDING_APPROVAL";
  const mutation = isResubmit ? resubmitOrder : mutations[targetState];

  if (!mutation) return null;

  async function handleClick() {
    try {
      await mutation.mutateAsync({ orderId: order.id });
      onTransitionComplete?.();
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <button
      onClick={handleClick}
      disabled={mutation.isPending}
      className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
    >
      {mutation.isPending ? "Working…" : label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Confirmation dialog (Cancel, Return) — has optional note field
// ---------------------------------------------------------------------------

function ConfirmTransitionButton({
  order,
  targetState,
  label,
  onTransitionComplete,
}: {
  order: OrderResponse;
  targetState: OrderState;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");

  const cancelOrder = useCancelOrder();
  const recordReturn = useRecordReturn();

  const mutation = targetState === "CANCELLED" ? cancelOrder : recordReturn;

  async function handleConfirm() {
    try {
      await mutation.mutateAsync({
        orderId: order.id,
        note: note || undefined,
      });
      setShowDialog(false);
      setNote("");
      onTransitionComplete?.();
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
      >
        {label}
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Confirm {label}
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This action is irreversible and will be recorded in the audit
              trail. Are you sure you want to{" "}
              {label.toLowerCase()} order {order.order_number}?
            </p>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700">
                Reason / Note (optional)
              </label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                maxLength={2000}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder="Optional reason for this action…"
              />
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowDialog(false);
                  setNote("");
                }}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Abort
              </button>
              <button
                onClick={handleConfirm}
                disabled={mutation.isPending}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {mutation.isPending ? "Working…" : `Confirm ${label}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Ship Dialog — line-by-line quantity entry for unshipped lines
// ---------------------------------------------------------------------------

function ShipDialog({
  order,
  label,
  onTransitionComplete,
}: {
  order: OrderResponse;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Build initial quantities: remaining = qty_ordered - qty_shipped
  const unshippedLines = (order.lines ?? []).filter((line) => {
    const remaining =
      Number(line.qty_ordered) - Number(line.qty_shipped);
    return remaining > 0;
  });

  const [quantities, setQuantities] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const line of unshippedLines) {
      const remaining =
        Number(line.qty_ordered) - Number(line.qty_shipped);
      initial[line.id] = String(remaining);
    }
    return initial;
  });

  const shipOrder = useShipOrder(order.id);

  function updateQty(lineId: string, value: string) {
    setQuantities((prev) => ({ ...prev, [lineId]: value }));
  }

  async function handleConfirm() {
    setError(null);

    // Build lines payload: only include lines with quantity > 0
    const lines = unshippedLines
      .map((line) => ({
        order_line_id: line.id,
        quantity: quantities[line.id] ?? "0",
      }))
      .filter((entry) => Number(entry.quantity) > 0);

    if (lines.length === 0) {
      setError("Ship at least one line with quantity greater than 0.");
      return;
    }

    try {
      await shipOrder.mutateAsync(lines);
      setShowDialog(false);
      // result.state tells us whether it's SHIPPED or PARTIALLY_FULFILLED
      // — the parent re-fetches and re-renders via onTransitionComplete
      onTransitionComplete?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ship failed");
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        {label}
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Ship Order {order.order_number}
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              Set the quantity to ship for each unshipped line. Lines not
              shipped will cause the order to become PARTIALLY_FULFILLED.
            </p>

            {unshippedLines.length === 0 ? (
              <p className="mb-4 text-sm text-gray-500">
                All lines are fully shipped.
              </p>
            ) : (
              <div className="mb-4 max-h-64 overflow-y-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs font-medium uppercase text-gray-500">
                      <th className="pb-2">Product</th>
                      <th className="pb-2 text-right">Ordered</th>
                      <th className="pb-2 text-right">Shipped</th>
                      <th className="pb-2 text-right">Remaining</th>
                      <th className="pb-2 text-right">Ship Qty</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {unshippedLines.map((line) => {
                      const remaining =
                        Number(line.qty_ordered) - Number(line.qty_shipped);
                      return (
                        <tr key={line.id}>
                          <td className="py-2 font-mono text-xs text-gray-700">
                            {line.product_id.slice(0, 8)}
                          </td>
                          <td className="py-2 text-right text-gray-900">
                            {line.qty_ordered}
                          </td>
                          <td className="py-2 text-right text-gray-500">
                            {line.qty_shipped}
                          </td>
                          <td className="py-2 text-right font-medium text-gray-900">
                            {remaining}
                          </td>
                          <td className="py-2 text-right">
                            <input
                              type="number"
                              min="0"
                              max={remaining}
                              step="any"
                              value={quantities[line.id] ?? "0"}
                              onChange={(e) =>
                                updateQty(line.id, e.target.value)
                              }
                              className="w-20 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {error && (
              <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowDialog(false);
                  setError(null);
                }}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={shipOrder.isPending}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {shipOrder.isPending ? "Shipping…" : "Confirm Ship"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Pay Dialog — amount, method, reference, note
// ---------------------------------------------------------------------------

function PayDialog({
  order,
  label,
  onTransitionComplete,
}: {
  order: OrderResponse;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [amount, setAmount] = useState(String(order.grand_total));
  const [method, setMethod] = useState("CASH");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [partialWarning, setPartialWarning] = useState<string | null>(null);

  const markPaid = useMarkPaid(order.id);

  async function handleConfirm() {
    setError(null);
    setPartialWarning(null);

    if (!amount || Number(amount) <= 0) {
      setError("Amount must be greater than 0.");
      return;
    }
    if (!method.trim()) {
      setError("Payment method is required.");
      return;
    }

    try {
      await markPaid.mutateAsync({
        amount,
        method: method.trim(),
        reference: reference || null,
        note: note || null,
      });
      setShowDialog(false);
      onTransitionComplete?.();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Payment failed";
      // Distinguish 409 partial-payment (payment recorded but order
      // not fully paid) from 422 validation errors.
      if (msg.includes("recorded") && msg.includes("remaining")) {
        setPartialWarning(msg);
        // Don't close the dialog — let the user see the warning and
        // decide whether to top up or close.
      } else {
        setError(msg);
      }
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        {label}
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Record Payment — {order.order_number}
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              Record a payment against this order's linked invoice. The full
              balance must be paid to transition the order to PAID.
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Amount *
                </label>
                <input
                  type="number"
                  min="0.01"
                  step="any"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Payment Method *
                </label>
                <select
                  value={method}
                  onChange={(e) => setMethod(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                >
                  <option value="CASH">Cash</option>
                  <option value="BANK_TRANSFER">Bank Transfer</option>
                  <option value="CHECK">Check</option>
                  <option value="CARD">Card</option>
                  <option value="OTHER">Other</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Reference (optional)
                </label>
                <input
                  type="text"
                  value={reference}
                  onChange={(e) => setReference(e.target.value)}
                  maxLength={200}
                  placeholder="e.g. check number, transaction ID"
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Note (optional)
                </label>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={2}
                  maxLength={2000}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>

            {partialWarning && (
              <div className="mt-4 rounded-md bg-amber-50 border border-amber-200 p-3 text-sm text-amber-800">
                <p className="font-medium">Payment recorded, but order not yet fully paid.</p>
                <p className="mt-1">{partialWarning}</p>
              </div>
            )}

            {error && (
              <div className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
                {error}
              </div>
            )}

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowDialog(false);
                  setError(null);
                  setPartialWarning(null);
                }}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                {partialWarning ? "Close" : "Cancel"}
              </button>
              {!partialWarning && (
                <button
                  onClick={handleConfirm}
                  disabled={markPaid.isPending}
                  className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {markPaid.isPending ? "Processing…" : "Confirm Payment"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
