/**
 * ReturnCreatePage — form to create a new customer return.
 *
 * Allows selecting a customer, warehouse, reason code, return type,
 * and adding line items with products and quantities.
 */
import { useState } from "react";
import { useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateReturn } from "@/api/hooks/useReturns";
import { useCustomers } from "@/api/hooks/useCustomers";
import { useWarehouses } from "@/api/hooks/useWarehouses";
import { useProducts } from "@/api/hooks/useProducts";
import { useReasonCodes } from "@/api/hooks/useReasonCodes";

export function ReturnCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const createReturn = useCreateReturn();
  const { data: customers } = useCustomers();
  const { data: warehouses } = useWarehouses();
  const { data: products } = useProducts();
  const { data: returnReasons } = useReasonCodes("RETURN");

  const [customerId, setCustomerId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [reasonCodeId, setReasonCodeId] = useState("");
  const [returnType, setReturnType] = useState("CUSTOMER_RETURN");
  const [note, setNote] = useState("");
  const [lines, setLines] = useState<Array<{
    product_id: string;
    qty_returned: string;
    unit_refund_amount: string;
  }>>([{ product_id: "", qty_returned: "", unit_refund_amount: "0" }]);
  const [error, setError] = useState<string | null>(null);

  function addLine() {
    setLines([...lines, { product_id: "", qty_returned: "", unit_refund_amount: "0" }]);
  }

  function removeLine(index: number) {
    setLines(lines.filter((_, i) => i !== index));
  }

  function updateLine(index: number, field: string, value: string) {
    const updated = [...lines];
    (updated[index] as any)[field] = value;
    setLines(updated);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!customerId) { setError(t("returns.validation.customerRequired")); return; }
    if (!warehouseId) { setError(t("returns.validation.warehouseRequired")); return; }
    if (!reasonCodeId) { setError(t("returns.validation.reasonRequired")); return; }

    const validLines = lines.filter(l => l.product_id && l.qty_returned && Number(l.qty_returned) > 0);
    if (validLines.length === 0) { setError(t("returns.validation.linesRequired")); return; }

    try {
      const result = await createReturn.mutateAsync({
        customer_id: customerId,
        warehouse_id: warehouseId,
        reason_code_id: reasonCodeId,
        return_type: returnType,
        note: note || null,
        lines: validLines.map(l => ({
          product_id: l.product_id,
          qty_returned: Number(l.qty_returned),
          unit_refund_amount: Number(l.unit_refund_amount) || 0,
        })),
      });
      navigate(`/office/returns/${(result as any).id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("returns.failedToCreate"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/returns" className="text-sm text-blue-600 hover:underline">
          {t("returns.backToReturns")}
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("returns.createTitle")}</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Header fields */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700">{t("returns.fields.customer")}</label>
              <select
                value={customerId}
                onChange={(e) => setCustomerId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">{t("returns.fields.selectCustomer")}</option>
                {(customers ?? []).map((c: any) => (
                  <option key={c.id} value={c.id}>{c.code} — {c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">{t("returns.fields.warehouse")}</label>
              <select
                value={warehouseId}
                onChange={(e) => setWarehouseId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">{t("returns.fields.selectWarehouse")}</option>
                {(warehouses ?? []).map((w: any) => (
                  <option key={w.id} value={w.id}>{w.code} — {w.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">{t("returns.fields.returnType")}</label>
              <select
                value={returnType}
                onChange={(e) => setReturnType(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="CUSTOMER_RETURN">{t("returns.typeOptions.CUSTOMER_RETURN")}</option>
                <option value="REP_RETURN_TO_FACTORY">{t("returns.typeOptions.REP_RETURN_TO_FACTORY")}</option>
                <option value="DAMAGED_RETURN">{t("returns.typeOptions.DAMAGED_RETURN")}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">{t("returns.fields.reason")}</label>
              <select
                value={reasonCodeId}
                onChange={(e) => setReasonCodeId(e.target.value)}
                required
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">{t("returns.fields.selectReason")}</option>
                {(returnReasons ?? []).map((rc: any) => (
                  <option key={rc.id} value={rc.id}>{rc.code} — {rc.label}</option>
                ))}
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="block text-sm font-medium text-gray-700">{t("returns.fields.note")}</label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={2}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                placeholder={t("returns.fields.notePlaceholder")}
              />
            </div>
          </div>
        </div>

        {/* Lines */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-900">{t("returns.fields.returnLines")}</h2>
            <button type="button" onClick={addLine} className="text-sm text-blue-600 hover:underline">
              {t("returns.fields.addLine")}
            </button>
          </div>

          {lines.map((line, index) => (
            <div key={index} className="mb-4 flex items-end gap-3">
              <div className="flex-1">
                <label className="block text-xs font-medium text-gray-500">{t("returns.fields.product")}</label>
                <select
                  value={line.product_id}
                  onChange={(e) => updateLine(index, "product_id", e.target.value)}
                  required
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                >
                  <option value="">{t("returns.fields.selectProduct")}</option>
                  {(products ?? []).map((p: any) => (
                    <option key={p.id} value={p.id}>{p.sku} — {p.name}</option>
                  ))}
                </select>
              </div>
              <div className="w-24">
                <label className="block text-xs font-medium text-gray-500">{t("returns.fields.qty")}</label>
                <input
                  type="number"
                  step="any"
                  min="0.01"
                  value={line.qty_returned}
                  onChange={(e) => updateLine(index, "qty_returned", e.target.value)}
                  required
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                />
              </div>
              <div className="w-28">
                <label className="block text-xs font-medium text-gray-500">{t("returns.fields.refund")}</label>
                <input
                  type="number"
                  step="any"
                  min="0"
                  value={line.unit_refund_amount}
                  onChange={(e) => updateLine(index, "unit_refund_amount", e.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                />
              </div>
              {lines.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeLine(index)}
                  className="mb-0.5 text-sm text-red-600 hover:underline"
                >
                  {t("returns.fields.remove")}
                </button>
              )}
            </div>
          ))}
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        <button
          type="submit"
          disabled={createReturn.isPending}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {createReturn.isPending ? t("returns.buttons.creating") : t("returns.buttons.create")}
        </button>
      </form>
    </div>
  );
}
