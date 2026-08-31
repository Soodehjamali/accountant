import { type FormEvent, useState } from "react";
import { useNavigate, Link, useSearchParams } from "react-router";
import { useCreateCreditNote } from "@/api/hooks/useCreditNotes";
import { useReasonCodes } from "@/api/hooks/useReasonCodes";
import { useInvoice } from "@/api/hooks/useInvoices";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";

interface LineInput {
  invoice_line_id: string;
  description: string;
  qty: string;
  unit_price: string;
}

const EMPTY_LINE: LineInput = {
  invoice_line_id: "",
  description: "",
  qty: "1",
  unit_price: "",
};

export function CreditNoteCreatePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const invoiceId = searchParams.get("invoice_id") ?? "";

  const { data: invoice } = useInvoice(invoiceId);
  const { data: reasonCodes } = useReasonCodes("RETURN");
  const createCreditNote = useCreateCreditNote();
  const canManage = usePermission(PERMISSIONS.CREDIT_NOTE_MANAGE);

  const [reasonCodeId, setReasonCodeId] = useState("");
  const [lines, setLines] = useState<LineInput[]>([{ ...EMPTY_LINE }]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          You do not have permission to create credit notes.
        </p>
        <Link
          to={ROUTES.OFFICE}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back
        </Link>
      </div>
    );
  }

  if (!invoiceId) {
    return (
      <div>
        <p className="text-red-600">
          An invoice_id is required to create a credit note.
        </p>
        <Link
          to={`${ROUTES.OFFICE}/invoices`}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to invoices
        </Link>
      </div>
    );
  }

  function updateLine(index: number, field: keyof LineInput, value: string) {
    setLines((prev) =>
      prev.map((line, i) => (i === index ? { ...line, [field]: value } : line)),
    );
  }

  function addLine() {
    setLines((prev) => [...prev, { ...EMPTY_LINE }]);
  }

  function removeLine(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!reasonCodeId) {
      setError("Reason code is required.");
      return;
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.description.trim()) {
        setError(`Line ${i + 1}: Description is required.`);
        return;
      }
      if (!line.qty || Number(line.qty) <= 0) {
        setError(`Line ${i + 1}: Quantity must be greater than 0.`);
        return;
      }
      if (!line.unit_price || Number(line.unit_price) <= 0) {
        setError(`Line ${i + 1}: Unit price must be greater than 0.`);
        return;
      }
    }

    try {
      await createCreditNote.mutateAsync({
        invoice_id: invoiceId,
        reason_code_id: reasonCodeId,
        lines: lines.map((line) => ({
          invoice_line_id: line.invoice_line_id || null,
          description: line.description,
          qty: line.qty,
          unit_price: line.unit_price,
        })),
        note: note || null,
      });
      navigate(`${ROUTES.OFFICE}/invoices/${invoiceId}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create credit note");
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.OFFICE}/invoices/${invoiceId}`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Back to invoice
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Create Credit Note
      </h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Header fields */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            Credit Note Details
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700">
                Invoice
              </label>
              <p className="mt-1 text-sm text-gray-900">
                {invoice?.invoice_number ?? invoiceId.slice(0, 8)}
              </p>
            </div>
            <div>
              <label
                htmlFor="reason_code"
                className="block text-sm font-medium text-gray-700"
              >
                Reason Code *
              </label>
              <select
                id="reason_code"
                value={reasonCodeId}
                onChange={(e) => setReasonCodeId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">Select reason…</option>
                {(reasonCodes ?? []).map((rc) => (
                  <option key={rc.id} value={rc.id}>
                    {rc.code} — {rc.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Line items */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">
              Line Items
            </h2>
            <button
              type="button"
              onClick={addLine}
              className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
            >
              + Add Line
            </button>
          </div>

          <div className="space-y-3">
            {lines.map((line, index) => (
              <div
                key={index}
                className="flex flex-wrap items-end gap-3 rounded border border-gray-200 bg-gray-50 p-3"
              >
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-500">
                    Invoice Line ID (optional)
                  </label>
                  <select
                    value={line.invoice_line_id}
                    onChange={(e) =>
                      updateLine(index, "invoice_line_id", e.target.value)
                    }
                    className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  >
                    <option value="">Unlinked</option>
                    {(invoice?.lines ?? []).map((il) => (
                      <option key={il.id} value={il.id}>
                        {il.description} ({il.qty} × {il.unit_price})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-500">
                    Description *
                  </label>
                  <input
                    type="text"
                    value={line.description}
                    onChange={(e) =>
                      updateLine(index, "description", e.target.value)
                    }
                    required
                    className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500">
                    Qty *
                  </label>
                  <input
                    type="number"
                    value={line.qty}
                    onChange={(e) => updateLine(index, "qty", e.target.value)}
                    required
                    min="0.01"
                    step="any"
                    className="mt-1 block w-20 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500">
                    Unit Price *
                  </label>
                  <input
                    type="number"
                    value={line.unit_price}
                    onChange={(e) =>
                      updateLine(index, "unit_price", e.target.value)
                    }
                    required
                    min="0.01"
                    step="any"
                    className="mt-1 block w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                  />
                </div>
                {lines.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeLine(index)}
                    className="rounded px-2 py-1 text-sm text-red-600 hover:bg-red-50"
                  >
                    ✕
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Note */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <label
            htmlFor="note"
            className="block text-sm font-medium text-gray-700"
          >
            Note (optional)
          </label>
          <textarea
            id="note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            maxLength={2000}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createCreditNote.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createCreditNote.isPending ? "Creating…" : "Create Credit Note"}
        </button>
      </form>
    </div>
  );
}
