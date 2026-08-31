import { useState } from "react";
import type { components } from "@/api/types";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";
import {
  useSubmitTransfer,
  useApproveTransfer,
  useDispatchTransfer,
  useReceiveTransfer,
  useCancelTransfer,
} from "@/api/hooks/useTransfers";

type TransferResponse = components["schemas"]["TransferResponse"];

/**
 * Transfer state machine — keys are the current state, values are the set
 * of directly reachable states.  Derived from
 * services/stock_transfer_service.py's ALLOWED_TRANSITIONS.
 */
export const ALLOWED_TRANSITIONS: Record<string, string[]> = {
  DRAFT: ["PENDING", "CANCELLED"],
  PENDING: ["APPROVED", "CANCELLED"],
  APPROVED: ["DISPATCHED"],
  DISPATCHED: ["RECEIVED"],
  RECEIVED: [],
  CANCELLED: [],
};

const ACTION_LABELS: Record<string, string> = {
  PENDING: "Submit",
  APPROVED: "Approve",
  DISPATCHED: "Dispatch",
  RECEIVED: "Receive",
  CANCELLED: "Cancel",
};

interface TransferTransitionActionsProps {
  transfer: TransferResponse;
  onTransitionComplete?: () => void;
}

export function TransferTransitionActions({
  transfer,
  onTransitionComplete,
}: TransferTransitionActionsProps) {
  const state = transfer.state as string;
  const allowed = ALLOWED_TRANSITIONS[state] ?? [];

  if (allowed.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {allowed.map((targetState) => (
        <TransitionButton
          key={targetState}
          transfer={transfer}
          targetState={targetState}
          onTransitionComplete={onTransitionComplete}
        />
      ))}
    </div>
  );
}

function TransitionButton({
  transfer,
  targetState,
  onTransitionComplete,
}: {
  transfer: TransferResponse;
  targetState: string;
  onTransitionComplete?: () => void;
}) {
  const canManage = usePermission(PERMISSIONS.TRANSFER_MANAGE);
  const label = ACTION_LABELS[targetState] ?? targetState;
  const isDestructive = targetState === "CANCELLED";

  // All transfer transitions require TRANSFER_MANAGE
  if (!canManage) return null;

  if (isDestructive) {
    return (
      <ConfirmTransitionButton
        transfer={transfer}
        targetState={targetState}
        label={label}
        onTransitionComplete={onTransitionComplete}
      />
    );
  }

  return (
    <SimpleTransitionButton
      transfer={transfer}
      targetState={targetState}
      label={label}
      onTransitionComplete={onTransitionComplete}
    />
  );
}

function SimpleTransitionButton({
  transfer,
  targetState,
  label,
  onTransitionComplete,
}: {
  transfer: TransferResponse;
  targetState: string;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const submitTransfer = useSubmitTransfer();
  const approveTransfer = useApproveTransfer();
  const dispatchTransfer = useDispatchTransfer();
  const receiveTransfer = useReceiveTransfer();

  const mutations: Record<
    string,
    { mutateAsync: (params: any) => Promise<any>; isPending: boolean }
  > = {
    PENDING: submitTransfer,
    APPROVED: approveTransfer,
    DISPATCHED: dispatchTransfer,
    RECEIVED: receiveTransfer,
  };

  const mutation = mutations[targetState];
  if (!mutation) return null;

  async function handleClick() {
    try {
      await mutation.mutateAsync({ transferId: transfer.id });
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

function ConfirmTransitionButton({
  transfer,
  targetState: _targetState,
  label,
  onTransitionComplete,
}: {
  transfer: TransferResponse;
  targetState: string;
  label: string;
  onTransitionComplete?: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const cancelTransfer = useCancelTransfer();

  async function handleConfirm() {
    try {
      await cancelTransfer.mutateAsync({
        transferId: transfer.id,
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
              Are you sure you want to {label.toLowerCase()} transfer{" "}
              {transfer.transfer_number}?
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
                disabled={cancelTransfer.isPending}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {cancelTransfer.isPending ? "Working…" : `Confirm ${label}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
