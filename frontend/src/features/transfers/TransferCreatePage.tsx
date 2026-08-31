import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useCreateTransfer } from "@/api/hooks/useTransfers";
import { useWarehouses } from "@/api/hooks/useWarehouses";
import { useProducts } from "@/api/hooks/useProducts";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";

interface LineInput {
  product_id: string;
  qty_requested: string;
  unit_cost: string;
  lot_id: string;
}

const EMPTY_LINE: LineInput = {
  product_id: "",
  qty_requested: "1",
  unit_cost: "0",
  lot_id: "",
};

export function TransferCreatePage() {
  const navigate = useNavigate();
  const canManage = usePermission(PERMISSIONS.TRANSFER_MANAGE);
  const { data: warehouses } = useWarehouses();
  const { data: products } = useProducts();
  const createTransfer = useCreateTransfer();

  const [sourceWarehouseId, setSourceWarehouseId] = useState("");
  const [destWarehouseId, setDestWarehouseId] = useState("");
  const [lines, setLines] = useState<LineInput[]>([{ ...EMPTY_LINE }]);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          You do not have permission to create stock transfers.
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

    if (!sourceWarehouseId) {
      setError("Source warehouse is required.");
      return;
    }
    if (!destWarehouseId) {
      setError("Destination warehouse is required.");
      return;
    }
    if (sourceWarehouseId === destWarehouseId) {
      setError("Source and destination warehouses must be different.");
      return;
    }

    const validLines = lines.filter(
      (l) => l.product_id && Number(l.qty_requested) > 0,
    );
    if (validLines.length === 0) {
      setError("At least one line with a product and quantity > 0 is required.");
      return;
    }

    try {
      await createTransfer.mutateAsync({
        source_warehouse_id: sourceWarehouseId,
        destination_warehouse_id: destWarehouseId,
        lines: validLines.map((l) => ({
          product_id: l.product_id,
          qty_requested: l.qty_requested,
          unit_cost: l.unit_cost || "0",
          lot_id: l.lot_id || null,
        })),
        note: note || null,
      });
      navigate(`${ROUTES.OFFICE}/transfers`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create transfer");
    }
  }

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

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Create Stock Transfer
      </h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Warehouse pickers */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            Transfer Details
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700">
                Source Warehouse *
              </label>
              <select
                value={sourceWarehouseId}
                onChange={(e) => setSourceWarehouseId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">Select source…</option>
                {(warehouses ?? []).map((wh: any) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.code} — {wh.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">
                Destination Warehouse *
              </label>
              <select
                value={destWarehouseId}
                onChange={(e) => setDestWarehouseId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">Select destination…</option>
                {(warehouses ?? []).map((wh: any) => (
                  <option key={wh.id} value={wh.id}>
                    {wh.code} — {wh.name}
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
              Transfer Lines
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
                    Product *
                  </label>
                  <select
                    value={line.product_id}
                    onChange={(e) =>
                      updateLine(index, "product_id", e.target.value)
                    }
                    className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 text-sm"
                  >
                    <option value="">Select product…</option>
                    {(products ?? []).map((p: any) => (
                      <option key={p.id} value={p.id}>
                        {p.sku} — {p.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500">
                    Qty *
                  </label>
                  <input
                    type="number"
                    value={line.qty_requested}
                    onChange={(e) =>
                      updateLine(index, "qty_requested", e.target.value)
                    }
                    required
                    min="0.01"
                    step="any"
                    className="mt-1 block w-24 rounded border border-gray-300 px-2 py-1 text-right text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500">
                    Unit Cost
                  </label>
                  <input
                    type="number"
                    value={line.unit_cost}
                    onChange={(e) =>
                      updateLine(index, "unit_cost", e.target.value)
                    }
                    min="0"
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
          disabled={createTransfer.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createTransfer.isPending ? "Creating…" : "Create Transfer"}
        </button>
      </form>
    </div>
  );
}
