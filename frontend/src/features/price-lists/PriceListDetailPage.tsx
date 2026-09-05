import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  useAddPriceEntry,
  usePriceEntries,
  usePriceList,
  useSetPriceListActive,
  useUpdatePriceEntry,
} from "@/api/hooks/usePriceLists";
import { useProducts } from "@/api/hooks/useProducts";
import { useDefaultCurrency } from "@/api/hooks/useCurrency";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

function formatDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

export function PriceListDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation("common");
  const canManage = usePermission(PERMISSIONS.PRICE_LIST_MANAGE);

  const { data: priceList, isLoading, error } = usePriceList(id ?? "");
  const { data: entries, isLoading: entriesLoading } = usePriceEntries(id ?? "");
  const { data: products } = useProducts(false);
  const { data: defaultCurrency } = useDefaultCurrency();
  const setActive = useSetPriceListActive();

  const [productId, setProductId] = useState("");
  const [unitPrice, setUnitPrice] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [formSuccess, setFormSuccess] = useState<string | null>(null);

  // "Update price" mini-form: which entry is being revised.
  const [updatingEntry, setUpdatingEntry] = useState<string | null>(null);
  const [updatePrice, setUpdatePrice] = useState("");
  const [updateEffectiveFrom, setUpdateEffectiveFrom] = useState("");
  const [updateReason, setUpdateReason] = useState("");

  const addEntry = useAddPriceEntry(id ?? "");
  const updateEntry = useUpdatePriceEntry(id ?? "");

  if (isLoading) {
    return <p className="text-gray-500">{t("status.loading")}</p>;
  }

  if (error || !priceList) {
    return (
      <div>
        <p className="text-red-600">{t("priceLists.notFound")}</p>
        <Link
          to="/office/price-lists"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("priceLists.backToPriceLists")}
        </Link>
      </div>
    );
  }

  const productMap = new Map(
    (products ?? []).map((p: any) => [p.id, p]),
  );

  const now = new Date();

  function resetForm() {
    setProductId("");
    setUnitPrice("");
    setEffectiveFrom("");
    setReason("");
    setFormError(null);
    setFormSuccess(null);
  }

  async function handleAddPrice(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    setFormSuccess(null);

    if (!productId) {
      setFormError(t("priceLists.selectProductFirst"));
      return;
    }
    const price = Number(unitPrice);
    if (!unitPrice || Number.isNaN(price) || price <= 0) {
      setFormError(t("priceLists.priceInvalid"));
      return;
    }
    const effective = effectiveFrom
      ? new Date(effectiveFrom).toISOString()
      : now.toISOString();

    try {
      await addEntry.mutateAsync({
        product_id: productId,
        unit_price: price,
        effective_from: effective,
        reason: reason || null,
      });
      setFormSuccess(t("priceLists.priceAdded"));
      resetForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t("priceLists.addFailed"));
    }
  }

  async function handleUpdatePrice(e: FormEvent) {
    e.preventDefault();
    if (!updatingEntry) return;
    setFormError(null);
    setFormSuccess(null);

    const price = Number(updatePrice);
    if (!updatePrice || Number.isNaN(price) || price <= 0) {
      setFormError(t("priceLists.priceInvalid"));
      return;
    }
    const effective = updateEffectiveFrom
      ? new Date(updateEffectiveFrom).toISOString()
      : now.toISOString();

    try {
      await updateEntry.mutateAsync({
        entryId: updatingEntry,
        unit_price: price,
        effective_from: effective,
        reason: updateReason || null,
      });
      setFormSuccess(t("priceLists.priceVersionCreated"));
      setUpdatingEntry(null);
      setUpdatePrice("");
      setUpdateEffectiveFrom("");
      setUpdateReason("");
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : t("priceLists.updateFailed"),
      );
    }
  }

  const currencyLabel =
    defaultCurrency && defaultCurrency.id === priceList.currency_id
      ? defaultCurrency.code
      : priceList.currency_id.slice(0, 8);

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/price-lists"
          className="text-sm text-blue-600 hover:underline"
        >
          {t("priceLists.backToPriceLists")}
        </Link>
      </div>

      {/* Header */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{priceList.name}</h1>
            <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-4">
              <div>
                <dt className="text-xs font-medium text-gray-500">
                  {t("priceLists.fields.priceType")}
                </dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {t(`priceLists.priceType.${priceList.price_type}`)}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500">
                  {t("priceLists.columns.currency")}
                </dt>
                <dd className="mt-0.5 text-sm text-gray-900">{currencyLabel}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500">
                  {t("priceLists.columns.scope")}
                </dt>
                <dd className="mt-0.5 text-sm text-gray-900">
                  {priceList.owner_scope}
                </dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-gray-500">
                  {t("priceLists.columns.status")}
                </dt>
                <dd className="mt-0.5">
                  <span
                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                      priceList.is_active
                        ? "bg-green-100 text-green-800"
                        : "bg-gray-100 text-gray-800"
                    }`}
                  >
                    {priceList.is_active
                      ? t("priceLists.active")
                      : t("priceLists.inactive")}
                  </span>
                </dd>
              </div>
            </dl>
          </div>
          {canManage && (
            <button
              onClick={() =>
                setActive.mutate({
                  priceListId: priceList.id,
                  active: !priceList.is_active,
                })
              }
              disabled={setActive.isPending}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {priceList.is_active
                ? t("priceLists.deactivate")
                : t("priceLists.activate")}
            </button>
          )}
        </div>
      </div>

      {/* Add price */}
      {canManage && (
        <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            {t("priceLists.addPriceTitle")}
          </h2>
          <form onSubmit={handleAddPrice} className="grid grid-cols-1 gap-4 sm:grid-cols-4">
            <div>
              <label
                htmlFor="entry_product"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.product")}
              </label>
              <select
                id="entry_product"
                value={productId}
                onChange={(e) => setProductId(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              >
                <option value="">{t("forms.selectOption")}</option>
                {(products ?? []).map((p: any) => (
                  <option key={p.id} value={p.id}>
                    {p.sku} — {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label
                htmlFor="entry_price"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.sellingPrice")}
              </label>
              <input
                id="entry_price"
                type="number"
                min="0.01"
                step="any"
                value={unitPrice}
                onChange={(e) => setUnitPrice(e.target.value)}
                placeholder="250,000"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="entry_effective"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.effectiveFrom")}
              </label>
              <input
                id="entry_effective"
                type="datetime-local"
                value={effectiveFrom}
                onChange={(e) => setEffectiveFrom(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="entry_reason"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.reason")}{" "}
                <span className="text-gray-400">{t("forms.optional")}</span>
              </label>
              <input
                id="entry_reason"
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={255}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div className="sm:col-span-4">
              <button
                type="submit"
                disabled={addEntry.isPending}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {addEntry.isPending
                  ? t("priceLists.saving")
                  : t("priceLists.addPrice")}
              </button>
              <p className="mt-2 text-xs text-gray-500">
                {t("priceLists.effectiveDefaultNote")}
              </p>
            </div>
          </form>
        </div>
      )}

      {/* Feedback */}
      {formError && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {formError}
        </div>
      )}
      {formSuccess && (
        <div className="mb-4 rounded-md bg-green-50 p-3 text-sm text-green-700">
          {formSuccess}
        </div>
      )}

      {/* Entries */}
      <div className="rounded-lg border border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {t("priceLists.entriesTitle")}
          </h2>
          <p className="mt-1 text-xs text-gray-500">
            {t("priceLists.entriesHelp")}
          </p>
        </div>

        {entriesLoading ? (
          <p className="p-6 text-gray-500">{t("status.loading")}</p>
        ) : (entries ?? []).length === 0 ? (
          <div className="p-6">
            <p className="text-sm text-gray-500">{t("priceLists.emptyEntries")}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("priceLists.columns.product")}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("priceLists.columns.unitPrice")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("priceLists.columns.effectiveFrom")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("priceLists.columns.effectiveTo")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("priceLists.columns.reason")}
                  </th>
                  {canManage && (
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      {t("common.actions")}
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(entries ?? []).map((entry: any) => {
                  const product = productMap.get(entry.product_id);
                  const isOpen = !entry.effective_to;
                  return (
                    <tr key={entry.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {product ? `${product.sku} — ${product.name}` : entry.product_id.slice(0, 8)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {Number(entry.unit_price).toLocaleString()}{" "}
                        <span className="text-xs text-gray-500">{currencyLabel}</span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                        {formatDateTime(entry.effective_from)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                        {isOpen ? (
                          <span className="text-xs font-medium text-green-700">
                            {t("priceLists.current")}
                          </span>
                        ) : (
                          formatDateTime(entry.effective_to)
                        )}
                      </td>
                      <td className="max-w-[180px] truncate px-4 py-3 text-sm text-gray-500">
                        {entry.reason ?? "—"}
                      </td>
                      {canManage && (
                        <td className="whitespace-nowrap px-4 py-3 text-sm">
                          {updatingEntry === entry.id ? (
                            <span className="text-xs text-gray-500">
                              {t("priceLists.updatingEntry")}
                            </span>
                          ) : (
                            <button
                              onClick={() => {
                                setUpdatingEntry(entry.id);
                                setUpdatePrice(String(entry.unit_price));
                                setUpdateEffectiveFrom("");
                                setUpdateReason("");
                              }}
                              className="text-blue-600 hover:text-blue-800 hover:underline"
                            >
                              {t("priceLists.updatePrice")}
                            </button>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Update-price (new version) form */}
      {canManage && updatingEntry && (
        <div className="mt-6 rounded-lg border border-blue-200 bg-blue-50 p-6">
          <h3 className="mb-1 text-sm font-semibold text-gray-900">
            {t("priceLists.updatePriceTitle")}
          </h3>
          <p className="mb-4 text-xs text-gray-600">
            {t("priceLists.versionNote")}
          </p>
          <form
            onSubmit={handleUpdatePrice}
            className="grid grid-cols-1 gap-4 sm:grid-cols-4"
          >
            <div>
              <label
                htmlFor="upd_price"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.sellingPrice")}
              </label>
              <input
                id="upd_price"
                type="number"
                min="0.01"
                step="any"
                value={updatePrice}
                onChange={(e) => setUpdatePrice(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="upd_effective"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.effectiveFrom")}
              </label>
              <input
                id="upd_effective"
                type="datetime-local"
                value={updateEffectiveFrom}
                onChange={(e) => setUpdateEffectiveFrom(e.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="upd_reason"
                className="block text-sm font-medium text-gray-700"
              >
                {t("priceLists.fields.reason")}{" "}
                <span className="text-gray-400">{t("forms.optional")}</span>
              </label>
              <input
                id="upd_reason"
                type="text"
                value={updateReason}
                onChange={(e) => setUpdateReason(e.target.value)}
                maxLength={255}
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div className="flex items-end gap-2">
              <button
                type="submit"
                disabled={updateEntry.isPending}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {updateEntry.isPending
                  ? t("priceLists.saving")
                  : t("priceLists.createVersion")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setUpdatingEntry(null);
                  setFormError(null);
                }}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                {t("common.cancel")}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
