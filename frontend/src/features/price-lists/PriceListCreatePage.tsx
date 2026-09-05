import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreatePriceList, type PriceType } from "@/api/hooks/usePriceLists";
import { useDefaultCurrency } from "@/api/hooks/useCurrency";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

const PRICE_TYPES: PriceType[] = ["RETAIL", "REP", "WHOLESALE", "EXPORT", "PROMO"];

export function PriceListCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const canManage = usePermission(PERMISSIONS.PRICE_LIST_MANAGE);
  const createPriceList = useCreatePriceList();
  const { data: defaultCurrency, isLoading: currencyLoading } =
    useDefaultCurrency();

  const [name, setName] = useState("");
  const [priceType, setPriceType] = useState<PriceType>("RETAIL");
  const [ownerScope, setOwnerScope] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("priceLists.noPermission")}</p>
        <Link
          to="/office/price-lists"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("priceLists.backToPriceLists")}
        </Link>
      </div>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!defaultCurrency) {
      setError(t("priceLists.currencyNotReady"));
      return;
    }

    try {
      const created = await createPriceList.mutateAsync({
        name,
        price_type: priceType,
        currency_id: defaultCurrency.id,
        owner_scope: ownerScope || t("priceLists.ownerScopeDefault"),
      });
      navigate(`/office/price-lists/${created.id}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("priceLists.failedToCreate"));
    }
  }

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

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        {t("priceLists.createTitle")}
      </h1>

      <form
        onSubmit={handleSubmit}
        className="max-w-2xl space-y-6 rounded-lg border border-gray-200 bg-white p-6"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label
              htmlFor="pl_name"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.name")}
            </label>
            <input
              id="pl_name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={160}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label
              htmlFor="pl_price_type"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.priceType")}
            </label>
            <select
              id="pl_price_type"
              value={priceType}
              onChange={(e) => setPriceType(e.target.value as PriceType)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              {PRICE_TYPES.map((pt) => (
                <option key={pt} value={pt}>
                  {t(`priceLists.priceType.${pt}`)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              htmlFor="pl_owner_scope"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.ownerScope")}
            </label>
            <input
              id="pl_owner_scope"
              type="text"
              value={ownerScope}
              onChange={(e) => setOwnerScope(e.target.value)}
              maxLength={255}
              placeholder={t("priceLists.fields.ownerScopePlaceholder")}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label
              htmlFor="pl_currency"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.currency")}
            </label>
            {currencyLoading ? (
              <p className="mt-2 text-sm text-gray-500">{t("status.loading")}</p>
            ) : defaultCurrency ? (
              <input
                id="pl_currency"
                type="text"
                value={`${defaultCurrency.code} (${defaultCurrency.symbol})`}
                disabled
                className="mt-1 block w-full rounded-md border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-600"
              />
            ) : (
              <p className="mt-2 text-sm text-red-600">
                {t("priceLists.currencyNotReady")}
              </p>
            )}
          </div>
        </div>

        <p className="text-xs text-gray-500">
          {t("priceLists.currencyNote")}
        </p>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createPriceList.isPending || !defaultCurrency}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createPriceList.isPending
            ? t("priceLists.creating")
            : t("priceLists.create")}
        </button>
      </form>
    </div>
  );
}
