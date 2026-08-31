import { useState } from "react";
import { useParams, Link } from "react-router";
import {
  useInvoice,
  useInvoiceHistory,
  useInvoicePayments,
  useIssueInvoice,
  useVoidInvoice,
} from "@/api/hooks/useInvoices";
import { useRecordPayment } from "@/api/hooks/usePayments";
import { useCreditNotes } from "@/api/hooks/useCreditNotes";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";
import type { components } from "@/api/types";

type InvoiceResponse = components["schemas"]["InvoiceResponse"];

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  ISSUED: "bg-blue-100 text-blue-800",
  PARTIALLY_PAID: "bg-amber-100 text-amber-800",
  PAID: "bg-green-100 text-green-800",
  CLOSED_CORRECTED: "bg-purple-100 text-purple-800",
  VOID: "bg-red-100 text-red-800",
};

export function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: invoice, isLoading, error, refetch } = useInvoice(id ?? "");
  const { data: history } = useInvoiceHistory(id ?? "");
  const canManage = usePermission(PERMISSIONS.INVOICE_MANAGE);

  const isDraft = invoice?.state === "DRAFT";
  const canPay =
    invoice?.state === "ISSUED" || invoice?.state === "PARTIALLY_PAID";

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !invoice) {
    return (
      <div>
        <p className="text-red-600">Invoice not found.</p>
        <Link
          to={`${ROUTES.OFFICE}/invoices`}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to invoices
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.OFFICE}/invoices`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Invoices
        </Link>
      </div>

      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          Invoice {invoice.invoice_number}
        </h1>
        <span
          className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${
            STATE_BADGE[invoice.state] ?? "bg-gray-100 text-gray-800"
          }`}
        >
          {invoice.state.replace(/_/g, " ")}
        </span>
      </div>

      {/* Action buttons */}
      {canManage && (
        <div className="mb-6 flex flex-wrap gap-2">
          {isDraft && (
            <IssueButton invoiceId={invoice.id} onDone={() => refetch()} />
          )}
          {isDraft && (
            <VoidButton invoiceId={invoice.id} onDone={() => refetch()} />
          )}
          {canPay && (
            <RecordPayButton invoice={invoice} onDone={() => refetch()} />
          )}
        </div>
      )}

      {/* Header fields */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-gray-500">Customer</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {invoice.customer_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Currency</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {invoice.currency_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Subtotal</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(invoice.subtotal).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Tax Total</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(invoice.tax_total).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Grand Total</dt>
            <dd className="mt-1 text-sm font-bold text-gray-900">
              {Number(invoice.grand_total).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Amount Paid</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(invoice.amount_paid).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Balance Due</dt>
            <dd className="mt-1 text-sm">
              <span
                className={
                  Number(invoice.balance_due) > 0
                    ? "font-medium text-amber-600"
                    : "text-gray-900"
                }
              >
                {Number(invoice.balance_due).toLocaleString()}
              </span>
            </dd>
          </div>
          {invoice.issued_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Issued At</dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(invoice.issued_at).toLocaleString()}
              </dd>
            </div>
          )}
          {invoice.due_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Due Date</dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(invoice.due_at).toLocaleDateString()}
              </dd>
            </div>
          )}
          {invoice.closed_at && (
            <div>
              <dt className="text-sm font-medium text-gray-500">Closed At</dt>
              <dd className="mt-1 text-sm text-gray-600">
                {new Date(invoice.closed_at).toLocaleString()}
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
                  Tax
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Discount
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Line Total
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {invoice.lines.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No line items.
                  </td>
                </tr>
              ) : (
                invoice.lines.map((line) => (
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
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
                      {Number(line.tax_amount) > 0
                        ? Number(line.tax_amount).toLocaleString()
                        : "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
                      {Number(line.discount_value) > 0
                        ? Number(line.discount_value).toLocaleString()
                        : "—"}
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

      {/* Payment history (for ISSUED/PARTIALLY_PAID/PAID invoices) */}
      {!isDraft && invoice.state !== "VOID" && (
        <InvoicePaymentsSection invoiceId={invoice.id} />
      )}

      {/* Credit Notes */}
      {!isDraft && invoice.state !== "VOID" && (
        <InvoiceCreditNotesSection invoice={invoice} />
      )}

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
// Payment history section
// ---------------------------------------------------------------------------

function InvoicePaymentsSection({ invoiceId }: { invoiceId: string }) {
  const { data: payments } = useInvoicePayments(invoiceId);

  return (
    <div className="mb-6">
      <h2 className="mb-3 text-lg font-semibold text-gray-900">
        Payments
      </h2>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Payment #
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                Amount
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Method
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Reference
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Date
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {(!payments || payments.length === 0) ? (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-8 text-center text-sm text-gray-500"
                >
                  No payments recorded.
                </td>
              </tr>
            ) : (
              payments.map((p) => (
                <tr key={p.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <Link
                      to={`${ROUTES.OFFICE}/payments/${p.id}`}
                      className="font-medium text-blue-600 hover:underline"
                    >
                      {p.payment_number}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                    {Number(p.amount).toLocaleString()}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {p.method}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {p.reference ?? "—"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {new Date(p.received_at).toLocaleDateString()}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Issue button (simple confirm with optional note)
// ---------------------------------------------------------------------------

function IssueButton({
  invoiceId,
  onDone,
}: {
  invoiceId: string;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const issueInvoice = useIssueInvoice(invoiceId);

  async function handleConfirm() {
    try {
      await issueInvoice.mutateAsync(note || null);
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
        Issue Invoice
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Issue Invoice
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This will transition the invoice from DRAFT to ISSUED. A customer
              ledger entry will be posted. This action cannot be undone.
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
                disabled={issueInvoice.isPending}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {issueInvoice.isPending ? "Issuing…" : "Confirm Issue"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Void button (DRAFT-only, confirmation dialog)
// ---------------------------------------------------------------------------

function VoidButton({
  invoiceId,
  onDone,
}: {
  invoiceId: string;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [note, setNote] = useState("");
  const voidInvoice = useVoidInvoice(invoiceId);

  async function handleConfirm() {
    try {
      await voidInvoice.mutateAsync(note || null);
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
        Void Invoice
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Void Invoice
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              This action is irreversible and will be recorded in the audit
              trail. Are you sure you want to void this invoice?
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
                disabled={voidInvoice.isPending}
                className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {voidInvoice.isPending ? "Voiding…" : "Confirm Void"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Credit Notes section
// ---------------------------------------------------------------------------

function InvoiceCreditNotesSection({ invoice }: { invoice: InvoiceResponse }) {
  const { data: creditNotes } = useCreditNotes({ invoice_id: invoice.id });
  const canManage = usePermission(PERMISSIONS.CREDIT_NOTE_MANAGE);
  const canCreateCreditNote =
    canManage &&
    ["ISSUED", "PARTIALLY_PAID", "PAID"].includes(invoice.state);

  return (
    <div className="mb-6">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Credit Notes
        </h2>
        {canCreateCreditNote && (
          <Link
            to={`${ROUTES.OFFICE}/credit-notes/new?invoice_id=${invoice.id}`}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            New Credit Note
          </Link>
        )}
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Credit Note #
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                State
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                Total Amount
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                Issued At
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {(!creditNotes || creditNotes.length === 0) ? (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-8 text-center text-sm text-gray-500"
                >
                  No credit notes.
                </td>
              </tr>
            ) : (
              creditNotes.map((cn) => (
                <tr key={cn.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <Link
                      to={`${ROUTES.OFFICE}/credit-notes/${cn.id}`}
                      className="font-medium text-blue-600 hover:underline"
                    >
                      {cn.credit_note_number}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                        cn.state === "APPLIED"
                          ? "bg-green-100 text-green-800"
                          : cn.state === "VOID"
                            ? "bg-red-100 text-red-800"
                            : cn.state === "ISSUED"
                              ? "bg-blue-100 text-blue-800"
                              : "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {cn.state}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                    {Number(cn.total_amount).toLocaleString()}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {cn.issued_at
                      ? new Date(cn.issued_at).toLocaleDateString()
                      : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Record Payment dialog (POST /payments, allocations-based)
// ---------------------------------------------------------------------------

function RecordPayButton({
  invoice,
  onDone,
}: {
  invoice: InvoiceResponse;
  onDone: () => void;
}) {
  const [showDialog, setShowDialog] = useState(false);
  const [amount, setAmount] = useState(String(invoice.balance_due));
  const [method, setMethod] = useState("CASH");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);

  const recordPayment = useRecordPayment();

  async function handleConfirm() {
    setError(null);

    if (!amount || Number(amount) <= 0) {
      setError("Amount must be greater than 0.");
      return;
    }
    if (Number(amount) > Number(invoice.balance_due)) {
      setError(
        `Amount (${amount}) exceeds balance due (${invoice.balance_due}).`,
      );
      return;
    }
    if (!method.trim()) {
      setError("Payment method is required.");
      return;
    }

    try {
      await recordPayment.mutateAsync({
        customer_id: invoice.customer_id,
        currency_id: invoice.currency_id,
        amount,
        method: method.trim(),
        reference: reference || null,
        allocations: [
          {
            invoice_id: invoice.id,
            allocated_amount: amount,
          },
        ],
      });
      setShowDialog(false);
      setAmount(String(invoice.balance_due));
      setMethod("CASH");
      setReference("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Payment failed");
    }
  }

  return (
    <>
      <button
        onClick={() => setShowDialog(true)}
        className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700"
      >
        Record Payment
      </button>

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-gray-900">
              Record Payment — {invoice.invoice_number}
            </h3>
            <p className="mb-4 text-sm text-gray-600">
              Balance due:{" "}
              <span className="font-medium">
                {Number(invoice.balance_due).toLocaleString()}
              </span>
            </p>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Amount *
                </label>
                <input
                  type="number"
                  min="0.01"
                  max={invoice.balance_due}
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
                  <option value="CHEQUE">Cheque</option>
                  <option value="CARD">Card</option>
                  <option value="MOBILE_WALLET">Mobile Wallet</option>
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
                  maxLength={120}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>

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
                }}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                disabled={recordPayment.isPending}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {recordPayment.isPending ? "Processing…" : "Confirm Payment"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
