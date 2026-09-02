import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useInvoices } from "@/api/hooks/useInvoices";
import { ROUTES } from "@/lib/constants";

const PAGE_SIZE = 50;

const INVOICE_STATES = [
  "DRAFT",
  "ISSUED",
  "PARTIALLY_PAID",
  "PAID",
  "CLOSED_CORRECTED",
  "VOID",
] as const;

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  ISSUED: "bg-blue-100 text-blue-800",
  PARTIALLY_PAID: "bg-amber-100 text-amber-800",
  PAID: "bg-green-100 text-green-800",
  CLOSED_CORRECTED: "bg-purple-100 text-purple-800",
  VOID: "bg-red-100 text-red-800",
};

export function InvoiceListPage() {
  const { t } = useTranslation("common");
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");

  const { data: invoices, isLoading, error } = useInvoices({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    state: stateFilter || undefined,
  });

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">{t("invoices.title")}</h1>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <select
          value={stateFilter}
          onChange={(e) => {
            setStateFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">{t("invoices.allStates")}</option>
          {INVOICE_STATES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="text-gray-500">{t("status.loading")}</p>}
      {error && <p className="text-red-600">{t("invoices.failedToLoad")}</p>}

      {!isLoading && !error && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.invoiceNumber")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.customer")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.state")}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.grandTotal")}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.balanceDue")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("invoices.columns.dueDate")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(invoices ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={6}
                      className="px-4 py-8 text-center text-sm text-gray-500"
                    >
                      {t("invoices.emptyState")}
                    </td>
                  </tr>
                ) : (
                  (invoices ?? []).map((inv) => (
                    <tr key={inv.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <Link
                          to={`${ROUTES.OFFICE}/invoices/${inv.id}`}
                          className="font-medium text-blue-600 hover:underline"
                        >
                          {inv.invoice_number}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs font-mono text-gray-500">
                        {inv.customer_id.slice(0, 8)}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            STATE_BADGE[inv.state] ?? "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {inv.state.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                        {Number(inv.grand_total).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                        <span
                          className={
                            Number(inv.balance_due) > 0
                              ? "font-medium text-amber-600"
                              : "text-gray-500"
                          }
                        >
                          {Number(inv.balance_due).toLocaleString()}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {inv.due_at
                          ? new Date(inv.due_at).toLocaleDateString()
                          : "—"}
                      </td>
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
            <span className="text-sm text-gray-500">{t("pagination.page", { page: page + 1 })}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(invoices ?? []).length < PAGE_SIZE}
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
