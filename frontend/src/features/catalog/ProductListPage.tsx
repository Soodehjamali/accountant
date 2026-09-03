import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useProducts, useDeleteProduct } from "@/api/hooks/useProducts";
import { useUnitsOfMeasure } from "@/api/hooks/useUnitsOfMeasure";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function ProductListPage() {
  const { t } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const { data: products, isLoading, error } = useProducts();
  const { data: uomList } = useUnitsOfMeasure();
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);
  const deleteProduct = useDeleteProduct();

  // Build a lookup map from UoM ID to name
  const uomMap = new Map<string, string>();
  for (const uom of uomList ?? []) {
    uomMap.set(uom.id, uom.name);
  }

  // Client-side filter (backend list endpoint has no search param — products
  // are master data and the list is small enough to filter in the browser).
  const filtered = (products ?? []).filter(
    (p) =>
      !search ||
      p.sku.toLowerCase().includes(search.toLowerCase()) ||
      p.name.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">{t("catalog.title")}</h1>
        {canManage && (
          <Link
            to="/office/catalog/new"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            {t("catalog.newProduct")}
          </Link>
        )}
      </div>

      {/* Search */}
      <div className="mb-4">
        <input
          type="text"
          placeholder={t("catalog.searchPlaceholder")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-md rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("catalog.failedToLoad")}</p>}

      {deleteError && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {deleteError}
        </div>
      )}

      {!isLoading && !error && (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.columns.sku")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.columns.name")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.columns.unit")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.columns.status")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.columns.lotTracked")}
                </th>
                {canManage && (
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("common.actions")}
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {filtered.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    {t("catalog.emptyState")}
                  </td>
                </tr>
              ) : (
                filtered.map((product) => (
                  <tr key={product.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <Link
                        to={`/office/catalog/${product.sku}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {product.sku}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {product.name}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-700">
                      {uomMap.get(product.base_uom_id) ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          product.status === "ACTIVE"
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {product.status}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {product.is_lot_tracked ? t("catalog.lotTracked.yes") : t("catalog.lotTracked.no")}
                    </td>
                    {canManage && (
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <div className="flex items-center gap-3">
                          <Link
                            to={`/office/catalog/edit/${product.id}`}
                            className="text-blue-600 hover:text-blue-800 hover:underline"
                          >
                            {t("common.edit")}
                          </Link>
                          <button
                            onClick={() => {
                              if (!window.confirm(t("common.confirmDelete"))) return;
                              setDeleteError(null);
                              deleteProduct.mutate(product.id, {
                                onError: (err) => setDeleteError(err.message),
                              });
                            }}
                            disabled={deleteProduct.isPending}
                            className="text-red-600 hover:text-red-800 hover:underline disabled:opacity-50"
                          >
                            {t("common.delete")}
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
