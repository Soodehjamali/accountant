import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  useAssignCustomerPriceList,
  useCustomerPriceLists,
  usePriceLists,
} from "@/api/hooks/usePriceLists";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/** Current-window assignment check (backend uses UTC now). */
function isCurrent(entry: any): boolean {
  const now = Date.now();
  const from = new Date(entry.effective_from).getTime();
  const to = entry.effective_to ? new Date(entry.effective_to).getTime() : Infinity;
  return from <= now && now < to;
}

export function CustomerPriceListSection({ customerId }: { customerId: string }) {
  const { t } = useTranslation("common");
  const canManage = usePermission(PERMISSIONS.PRICE_LIST_MANAGE);

  const { data: assignments, isLoading } = useCustomerPriceLists(customerId);
  const { data: priceLists } = usePriceLists();
  const assign = useAssignCustomerPriceList(customerId);

  const [priceListId, setPriceListId] = useState("");
  const [priority, setPriority] = useState("1");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const priceListMap = new Map((priceLists ?? []).map((p: any) => [p.id, p]));

  async function handleAssign(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    if (!priceListId) {
      setError(t("priceLists.assignFailed"));
      return;
    }
    const prio = Number(priority);
    if (!Number.isInteger(prio) || prio < 1) {
      setError(t("priceLists.assignFailed"));
      return;
    }
    try {
      await assign.mutateAsync({
        price_list_id: priceListId,
        effective_from: effectiveFrom
          ? new Date(effectiveFrom).toISOString()
          : new Date().toISOString(),
        priority: prio,
      });
      setMessage(t("priceLists.assigned"));
      setPriceListId("");
      setPriority("1");
      setEffectiveFrom("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("priceLists.assignFailed"));
    }
  }

  return (
    <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
      <h2 className="text-lg font-semibold text-gray-900">
        {t("priceLists.customerAssignTitle")}
      </h2>
      <p className="mt-1 text-xs text-gray-500">
        {t("priceLists.customerAssignHelp")}
      </p>

      {isLoading ? (
        <p className="mt-3 text-sm text-gray-500">{t("status.loading")}</p>
      ) : (assignments ?? []).length === 0 ? (
        <p className="mt-3 text-sm text-gray-500">
          {t("priceLists.noAssignments")}
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.name")}
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.fields.priority")}
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.effectiveFrom")}
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.effectiveTo")}
                </th>
                <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  {t("priceLists.columns.status")}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {(assignments ?? []).map((a: any) => {
                const pl = priceListMap.get(a.price_list_id);
                const current = isCurrent(a);
                return (
                  <tr key={a.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-3 py-2 text-sm text-gray-900">
                      {pl ? pl.name : a.price_list_id.slice(0, 8)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-sm text-gray-600">
                      {a.priority}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-sm text-gray-600">
                      {fmtDate(a.effective_from)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-sm text-gray-600">
                      {fmtDate(a.effective_to)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-sm">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          current
                            ? "bg-green-100 text-green-800"
                            : "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {current
                          ? t("priceLists.assignment.activeBadge")
                          : t("priceLists.assignment.expiredBadge")}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {message && (
        <div className="mt-3 rounded-md bg-green-50 p-3 text-sm text-green-700">
          {message}
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {canManage && (
        <form
          onSubmit={handleAssign}
          className="mt-4 grid grid-cols-1 gap-3 border-t border-gray-100 pt-4 sm:grid-cols-4"
        >
          <div>
            <label
              htmlFor="assign_pl"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.assignNew")}
            </label>
            <select
              id="assign_pl"
              value={priceListId}
              onChange={(e) => setPriceListId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">{t("forms.selectOption")}</option>
              {(priceLists ?? [])
                .filter((p: any) => p.is_active)
                .map((p: any) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </div>
          <div>
            <label
              htmlFor="assign_priority"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.priority")}
            </label>
            <input
              id="assign_priority"
              type="number"
              min={1}
              step={1}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label
              htmlFor="assign_effective"
              className="block text-sm font-medium text-gray-700"
            >
              {t("priceLists.fields.effectiveFrom")}
            </label>
            <input
              id="assign_effective"
              type="datetime-local"
              value={effectiveFrom}
              onChange={(e) => setEffectiveFrom(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div className="flex items-end">
            <button
              type="submit"
              disabled={assign.isPending}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {assign.isPending ? t("priceLists.saving") : t("priceLists.assignButton")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
