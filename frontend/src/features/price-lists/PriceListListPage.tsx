import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { usePriceLists, useSetPriceListActive } from "@/api/hooks/usePriceLists";
import { useDefaultCurrency } from "@/api/hooks/useCurrency";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

function currencyLabel(currencyId: string, defaultCurrency?: { id: string; code: string } | null) {
  if (defaultCurrency && defaultCurrency.id === currencyId) {
    return defaultCurrency.code;
  }
  return currencyId.slice(0, 8);
}

export function PriceListListPage() {
  const { t } = useTranslation("common");
  const canManage = usePermission(PERMISSIONS.PRICE_LIST_MANAGE);

  const { data: priceLists, isLoading, error } = usePriceLists();
  const { data: defaultCurrency } = useDefaultCurrency();
  const setActive = useSetPriceListActive();

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">
          {t("priceLists.title")}
        </h1>
        {canManage && (
          <Link
            to="/office/price-lists/new"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            {t("priceLists.newPriceList")}
          </Link>
        )}
      </div>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("priceLists.failedToLoad")}</p>}

      {!isLoading && !error && (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.name")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.priceType")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.currency")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.scope")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.status")}
                </th>
                {canManage && (
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("common.actions")}
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(priceLists ?? []).length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-12 text-center">
                    <p className="text-sm text-gray-500">
                      {t("priceLists.emptyState")}
                    </p>
                    {canManage && (
                      <Link
                        to="/office/price-lists/new"
                        className="mt-3 inline-block text-sm font-medium text-blue-600 hover:underline"
                      >
                        {t("priceLists.newPriceList")}
                      </Link>
                    )}
                  </td>
                </tr>
              ) : (
                (priceLists ?? []).map((pl: any) => (
                  <tr key={pl.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                      <Link
                        to={`/office/price-lists/${pl.id}`}
                        className="text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {pl.name}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                      {t(`priceLists.priceType.${pl.price_type}`)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                      {currencyLabel(pl.currency_id, defaultCurrency)}
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-sm text-gray-500">
                      {pl.owner_scope}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          pl.is_active
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {pl.is_active
                          ? t("priceLists.active")
                          : t("priceLists.inactive")}
                      </span>
                    </td>
                    {canManage && (
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <div className="flex items-center gap-3">
                          <Link
                            to={`/office/price-lists/${pl.id}`}
                            className="text-blue-600 hover:text-blue-800 hover:underline"
                          >
                            {t("priceLists.managePrices")}
                          </Link>
                          <button
                            onClick={() =>
                              setActive.mutate({
                                priceListId: pl.id,
                                active: !pl.is_active,
                              })
                            }
                            disabled={setActive.isPending}
                            className="text-gray-600 hover:text-gray-900 hover:underline disabled:opacity-50"
                          >
                            {pl.is_active
                              ? t("priceLists.deactivate")
                              : t("priceLists.activate")}
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
