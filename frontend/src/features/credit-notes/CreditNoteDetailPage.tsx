import { useState } from "react";
import { useParams, Link } from "react-router";
import {
  useCreditNote,
  useIssueCreditNote,
  useApplyCreditNote,
  useVoidCreditNote,
} from "@/api/hooks/useCreditNotes";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  ISSUED: "bg-blue-100 text-blue-800",
  APPLIED: "bg-green-100 text-green-800",
  VOID: "bg-red-100 text-red-800",
};

export function CreditNoteDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: creditNote, isLoading, error, refetch } = useCreditNote(id ?? "");
  const canManage = usePermission(PERMISSIONS.CREDIT_NOTE_MANAGE);

  const isDraft = creditNote?.state === "DRAFT";
  const isIssued = creditNote?.state === "ISSUED";

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !creditNote) {
    return (
      <div>
        <p className="text-red-600">Credit note not found.</p>
        <Link
          to={ROUTES.OFFICE}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to office
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.OFFICE}/invoices/${creditNote.invoice_id}`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Back to invoice
        </Link>
      </div>

      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          Credit Note {creditNote.credit_note_number}
        </h1>
        <span
          className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${
            STATE_BADGE[creditNote.state] ?? "bg-gray-100 text-gray-800"
          }`}
        >
          {creditNote.state}
        </span>
      </div>

      {/* Action buttons */}
      {canManage && (
        <div className="mb-6 flex flex-wrap gap-2">
          {isDraft && (
            <IssueButton
              creditNoteId={creditNote.id}
              onDone={() => refetch()}
            />
          )}
          {isDraft && (
            <VoidButton
              creditNoteId={creditNote.id}
              onDone={() => refetch()}
            />
          )}
          {isIssued && (
            <ApplyButton
              creditNoteId={creditNote.id}
              creditNoteNumber={creditNote.credit_note_number}
              onDone={() => refetch()}
            />
          )}
        </div>
      )}

      {/* Header fields */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-gray-500">Invoice</dt>
            <dd className="mt-1">
              <Link
                to={`${ROUTES.OFFICE}/invoices/${creditNote.invoice_id}`}
                className="text-sm font-medium text-blue-600 hover:underline"
              >
                {creditNote.invoice_id.slice(0, 8)}
              </Link>
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Customer</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {creditNote.customer_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Total Amount
            </dt>
            <dd className="mt-1 text-sm font-bold text-gray-900">
              {Number(creditNote.total_amount).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Reason Code ID
            </dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {creditNote.reason_code_id}
            </dd>
          </div>
          {creditNote.reference_type && (
            <div>
              <dt className="text-sm font-medium text-gray-500">
                Reference Type
              </dt>
              <dd className="mt-1 text-sm text-gray-900">
                {creditNote.reference_type}
              </dd>
            </div>
          )}
          {creditNote.issued_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">
                Issued At
              </dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(creditNote.issued_at).toLocaleString()}
              </dd>
            </div>
          )}
          <div>
            <dt className="text-sm font-medium text-gray-500">Created</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(creditNote.created_at).toLocaleString()}
            </dd>
          </div>
        </dl>
      </div>

      {/* Line items */}
      <div>
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Line Items
        </h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Description
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Qty
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
              {creditNote.lines.length === 0 ? (
                <tr>
                  <td
                    colSpan={4}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No line items.
                  </td>
                </tr>
              ) : (
                creditNote.lines.map((line) => (
                  <tr key={line.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 text-sm text-gray-900">
                      {line.description}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                      {line.qty}
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Issue button (DRAFT -> ISSUED, simple confirm)
// ---------------------------------------------------------------------------

function IssueButton({
  creditNoteId,
  onDone,
}: {
  creditNoteId: string;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const issueCreditNote = useIssueCreditNote(creditNoteId);

  async function handleConfirm() {
    try {
      await issueCreditNote.mutateAsync(note || null);
      setShowDialog(false);
      setNote("");
      onDone();
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
      >
        Issue Credit Note
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Issue Credit Note
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This will transition the credit note from DRAFT to ISSUED. After
              issuance it is no longer mutable.
            </p>
            <div className="mb-4">
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
            <div className="flex justify-end gap-3">
              <button
                onClick={() => {
                  setShowDialog(false);
                  setNote("");
                }}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={issueCreditNote.isPending}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {issueCreditNote.isPending ? "Issuing…" : "Confirm Issue"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Apply button (ISSUED -> APPLIED, destructive confirm)
// ---------------------------------------------------------------------------

function ApplyButton({
  creditNoteId,
  creditNoteNumber,
  onDone,
}: {
  creditNoteId: string;
  creditNoteNumber: string;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const applyCreditNote = useApplyCreditNote(creditNoteId);

  async function handleConfirm() {
    try {
      await applyCreditNote.mutateAsync(note || null);
      setShowDialog(false);
      setNote("");
      onDone();
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700"
      >
        Apply Credit Note
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Apply Credit Note
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This action is <span className="font-medium">irreversible</span>.
              Applying credit note {creditNoteNumber} will close the original
              invoice as corrected (CLOSED_CORRECTED) and post a customer
              ledger entry. Are you sure?
            </p>
            <div className="mb-4">
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
                disabled={applyCreditNote.isPending}
                className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {applyCreditNote.isPending ? "Applying…" : "Confirm Apply"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Void button (DRAFT -> VOID, destructive confirm)
// ---------------------------------------------------------------------------

function VoidButton({
  creditNoteId,
  onDone,
}: {
  creditNoteId: string;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const voidCreditNote = useVoidCreditNote(creditNoteId);

  async function handleConfirm() {
    try {
      await voidCreditNote.mutateAsync(note || null);
      setShowDialog(false);
      setNote("");
      onDone();
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
        Void Credit Note
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Void Credit Note
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This action is irreversible and will be recorded in the audit
              trail. Are you sure you want to void this credit note?
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
                placeholder="Optional reason for voiding…"
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
                disabled={voidCreditNote.isPending}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {voidCreditNote.isPending ? "Voiding…" : "Confirm Void"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
