import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useProductCategories, useDeleteProductCategory } from "@/api/hooks/useProductCategories";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function ProductCategoryListPage() {
  const { t } = useTranslation("common");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);
  const deleteCategory = useDeleteProductCategory();

  const { data: categories, isLoading, error } = useProductCategories();

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">{t("catalog.categories.title")}</h1>
      </div>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("catalog.categories.failedToLoad")}</p>}

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
                  {t("catalog.categories.columns.code")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.categories.columns.name")}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("catalog.categories.columns.level")}
                </th>
                {canManage && (
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("common.actions")}
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(categories ?? []).length === 0 ? (
                <tr>
                  <td
                    colSpan={canManage ? 4 : 3}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    {t("catalog.categories.emptyState")}
                  </td>
                </tr>
              ) : (
                (categories ?? []).map((cat: any) => (
                  <tr key={cat.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                      {cat.code}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {cat.name}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {cat.level}
                    </td>
                    {canManage && (
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <button
                          onClick={() => {
                            if (!window.confirm(t("common.confirmDelete"))) return;
                            setDeleteError(null);
                            deleteCategory.mutate(cat.id, {
                              onError: (err) => setDeleteError(err.message),
                            });
                          }}
                          disabled={deleteCategory.isPending}
                          className="text-red-600 hover:text-red-800 hover:underline disabled:opacity-50"
                        >
                          {t("common.delete")}
                        </button>
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
