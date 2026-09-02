import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateOrder } from "@/api/hooks/useOrders";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";

interface LineInput {
  product_id: string;
  fulfillment_warehouse_id: string;
  qty_ordered: string;
  unit_price: string;
}

const EMPTY_LINE: LineInput = {
  product_id: "",
  fulfillment_warehouse_id: "",
  qty_ordered: "1",
  unit_price: "",
};

export function OrderCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const createOrder = useCreateOrder();
  const canManage = usePermission(PERMISSIONS.ORDER_MANAGE);

  const [customerId, setCustomerId] = useState("");
  const [representativeId, setRepresentativeId] = useState("");
  const [currencyId, setCurrencyId] = useState("");
  const [orderType, setOrderType] = useState<"LOCAL" | "DIRECT">("LOCAL");
  const [fulfillmentMode, setFulfillmentMode] = useState<
    "REP_LOCAL" | "FACTORY_DIRECT"
  >("REP_LOCAL");
  const [salesChannel, setSalesChannel] = useState("OFFICE");
  const [lines, setLines] = useState<LineInput[]>([{ ...EMPTY_LINE }]);
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          {t("orders.noPermission")}
        </p>
        <Link
          to={`${ROUTES.OFFICE}/orders`}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("orders.backToOrders")}
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

    // Client-side required-field validation only
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.product_id.trim()) {
        setError(t("orders.validation.lineProductRequired", { line: i + 1 }));
        return;
      }
      if (!line.fulfillment_warehouse_id.trim()) {
        setError(t("orders.validation.lineWarehouseRequired", { line: i + 1 }));
        return;
      }
      if (!line.qty_ordered || Number(line.qty_ordered) <= 0) {
        setError(t("orders.validation.lineQtyRequired", { line: i + 1 }));
        return;
      }
    }

    try {
      await createOrder.mutateAsync({
        customer_id: customerId,
        representative_id: representativeId,
        currency_id: currencyId,
        order_type: orderType,
        fulfillment_mode: fulfillmentMode,
        sales_channel: salesChannel,
        lines: lines.map((line) => ({
          product_id: line.product_id,
          fulfillment_warehouse_id: line.fulfillment_warehouse_id,
          qty_ordered: line.qty_ordered,
          fulfillment_mode: fulfillmentMode,
        })),
      });
      navigate(`${ROUTES.OFFICE}/orders`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("orders.failedToCreate"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.OFFICE}/orders`}
          className="text-sm text-blue-600 hover:underline"
        >
          {t("orders.backToOrders")}
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("orders.createTitle")}</h1>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Header fields */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            {t("orders.fields.orderDetails")}
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <label
                htmlFor="customer_id"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.customerId")}
              </label>
              <input
                id="customer_id"
                type="text"
                value={customerId}
                onChange={(e) => setCustomerId(e.target.value)}
                required
                placeholder={t("forms.uuidPlaceholder")}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="representative_id"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.representativeId")}
              </label>
              <input
                id="representative_id"
                type="text"
                value={representativeId}
                onChange={(e) => setRepresentativeId(e.target.value)}
                required
                placeholder={t("forms.uuidPlaceholder")}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="currency_id"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.currencyId")}
              </label>
              <input
                id="currency_id"
                type="text"
                value={currencyId}
                onChange={(e) => setCurrencyId(e.target.value)}
                required
                placeholder={t("forms.uuidPlaceholder")}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="order_type"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.orderType")}
              </label>
              <select
                id="order_type"
                value={orderType}
                onChange={(e) =>
                  setOrderType(e.target.value as "LOCAL" | "DIRECT")
                }
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="LOCAL">{t("orders.typeOptions.LOCAL")}</option>
                <option value="DIRECT">{t("orders.typeOptions.DIRECT")}</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="fulfillment_mode"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.fulfillmentMode")}
              </label>
              <select
                id="fulfillment_mode"
                value={fulfillmentMode}
                onChange={(e) =>
                  setFulfillmentMode(
                    e.target.value as "REP_LOCAL" | "FACTORY_DIRECT",
                  )
                }
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="REP_LOCAL">{t("orders.fulfillmentOptions.REP_LOCAL")}</option>
                <option value="FACTORY_DIRECT">{t("orders.fulfillmentOptions.FACTORY_DIRECT")}</option>
              </select>
            </div>
            <div>
              <label
                htmlFor="sales_channel"
                className="block text-sm font-medium text-gray-700"
              >
                {t("orders.fields.salesChannel")}
              </label>
              <input
                id="sales_channel"
                type="text"
                value={salesChannel}
                onChange={(e) => setSalesChannel(e.target.value)}
                required
                maxLength={24}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>

        {/* Line items */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">
              {t("orders.fields.lineItems")}
            </h2>
            <button
              type="button"
              onClick={addLine}
              className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50"
            >
              {t("orders.fields.addLine")}
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
                    {t("orders.fields.productId")}
                  </label>
                  <input
                    type="text"
                    value={line.product_id}
                    onChange={(e) =>
                      updateLine(index, "product_id", e.target.value)
                    }
                    required
                    placeholder={t("forms.uuidPlaceholder")}
                    className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 text-sm font-mono"
                  />
                </div>
                <div className="flex-1">
                  <label className="block text-xs font-medium text-gray-500">
                    {t("orders.fields.warehouseId")}
                  </label>
                  <input
                    type="text"
                    value={line.fulfillment_warehouse_id}
                    onChange={(e) =>
                      updateLine(
                        index,
                        "fulfillment_warehouse_id",
                        e.target.value,
                      )
                    }
                    required
                    placeholder={t("forms.uuidPlaceholder")}
                    className="mt-1 block w-full rounded border border-gray-300 px-2 py-1 text-sm font-mono"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500">
                    {t("orders.fields.qty")}
                  </label>
                  <input
                    type="number"
                    value={line.qty_ordered}
                    onChange={(e) =>
                      updateLine(index, "qty_ordered", e.target.value)
                    }
                    required
                    min="0.01"
                    step="any"
                    className="mt-1 block w-20 rounded border border-gray-300 px-2 py-1 text-right text-sm"
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

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createOrder.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createOrder.isPending ? t("orders.buttons.creating") : t("orders.buttons.create")}
        </button>
      </form>
    </div>
  );
}
