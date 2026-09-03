import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useRepresentatives, useDeleteRepresentative } from "@/api/hooks/useRepresentatives";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

const PAGE_SIZE = 50;

export function RepresentativeListPage() {
  const { t } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const canManage = usePermission(PERMISSIONS.REPRESENTATIVE_MANAGE);
  const deleteRepresentative = useDeleteRepresentative();

  const { data: representatives, isLoading, error } = useRepresentatives();

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">{t("representatives.title")}</h1>
        {canManage && (
          <Link
            to="/office/representatives/new"
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            {t("representatives.addRepresentative")}
          </Link>
        )}
      </div>

      {/* Search */}
      <div className="mb-4 flex flex-wrap gap-3">
        <input
          type="text"
          placeholder={t("representatives.searchPlaceholder")}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          className="w-full max-w-md rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("representatives.failedToLoad")}</p>}

      {deleteError && (
        <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {deleteError}
        </div>
      )}

      {!isLoading && !error && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("representatives.columns.code")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("representatives.columns.name")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("representatives.columns.nationalId")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("representatives.columns.taxId")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("representatives.columns.status")}
                  </th>
                  {canManage && (
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      {t("common.actions")}
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(representatives ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-4 py-12 text-center"
                    >
                      <p className="text-sm text-gray-500">{t("representatives.emptyState")}</p>
                      {canManage && (
                        <Link
                          to="/office/representatives/new"
                          className="mt-3 inline-block text-sm font-medium text-blue-600 hover:underline"
                        >
                          {t("representatives.addRepresentative")}
                        </Link>
                      )}
                    </td>
                  </tr>
                ) : (
                  (representatives ?? []).map((rep: any) => (
                    <tr key={rep.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                        {rep.code}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {rep.person_name}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {rep.national_id || "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {rep.tax_id || "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            rep.status === "ACTIVE"
                              ? "bg-green-100 text-green-800"
                              : "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {rep.status}
                        </span>
                      </td>
                      {canManage && (
                        <td className="whitespace-nowrap px-4 py-3 text-sm">
                          <div className="flex items-center gap-3">
                            <Link
                              to={`/office/representatives/edit/${rep.id}`}
                              className="text-blue-600 hover:text-blue-800 hover:underline"
                            >
                              {t("common.edit")}
                            </Link>
                            <button
                              onClick={() => {
                                if (!window.confirm(t("common.confirmDelete"))) return;
                                setDeleteError(null);
                                deleteRepresentative.mutate(rep.id, {
                                  onError: (err) => setDeleteError(err.message),
                                });
                              }}
                              disabled={deleteRepresentative.isPending}
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

          {/* Pagination */}
          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {t("pagination.previous")}
            </button>
            <span className="text-sm text-gray-500">
              {t("pagination.page", { page: page + 1 })}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(representatives ?? []).length < PAGE_SIZE}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {t("pagination.next")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
