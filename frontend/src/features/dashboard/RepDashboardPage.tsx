/**
 * RepDashboardPage — compact summary for the Representative Portal.
 *
 * Shows:
 * - Commission balance card (GET /representatives/{id}/commission-balance)
 * - Count of open/in-progress orders
 * - Recent orders mini-list
 */
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCommissionBalance } from "@/api/hooks/useCommissions";
import { useOrders } from "@/api/hooks/useOrders";
import { ROUTES } from "@/lib/constants";
import { formatCurrency, formatDate } from "@/lib/format";

const STATE_BADGE: Record<string, string> = {
  DRAFT: "bg-gray-100 text-gray-800",
  PENDING_APPROVAL: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  RESERVED: "bg-indigo-100 text-indigo-800",
  FULFILLING: "bg-purple-100 text-purple-800",
  SHIPPED: "bg-cyan-100 text-cyan-800",
  INVOICED: "bg-teal-100 text-teal-800",
  PAID: "bg-green-100 text-green-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  CANCELLED: "bg-red-100 text-red-800",
  BACKORDERED: "bg-orange-100 text-orange-800",
  PARTIALLY_FULFILLED: "bg-amber-100 text-amber-800",
  RETURNED: "bg-rose-100 text-rose-800",
};

/** States considered "open" or "in-progress" for the dashboard count. */
const ACTIVE_STATES = [
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "RESERVED",
  "FULFILLING",
  "SHIPPED",
  "BACKORDERED",
  "PARTIALLY_FULFILLED",
];

export function RepDashboardPage() {
  const { t } = useTranslation(["dashboard", "common"]);

  const {
    data: recentOrders,
    isLoading: ordersLoading,
  } = useOrders({ limit: 5 });

  const {
    data: activeOrders,
  } = useOrders({ limit: 100 });

  // Derive representative_id from orders (all scoped to the caller's rep).
  const representativeId =
    recentOrders?.[0]?.representative_id ?? "";

  const {
    data: balanceData,
    isLoading: balanceLoading,
  } = useCommissionBalance(representativeId);

  const activeCount = (activeOrders ?? []).filter((o) =>
    ACTIVE_STATES.includes(o.state),
  ).length;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        {t("title")}
      </h1>

      {/* Summary cards */}
      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Commission Balance Card */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="text-sm font-medium text-gray-500">
            {t("commissionBalance")}
          </h2>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {balanceLoading
              ? "…"
              : balanceData
                ? formatCurrency(balanceData.balance)
                : "—"}
          </p>
          {representativeId && (
            <Link
              to={`${ROUTES.REP}/commission`}
              className="mt-2 inline-block text-sm text-blue-600 hover:underline"
            >
              {t("common:common.viewDetails")}
            </Link>
          )}
        </div>

        {/* Active Orders Card */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="text-sm font-medium text-gray-500">
            {t("activeOrders")}
          </h2>
          <p className="mt-2 text-3xl font-bold text-gray-900">
            {ordersLoading ? "…" : activeCount}
          </p>
          <Link
            to={`${ROUTES.REP}/orders`}
            className="mt-2 inline-block text-sm text-blue-600 hover:underline"
          >
            {t("common:common.viewAll")}
          </Link>
        </div>

        {/* Quick Links Card */}
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="text-sm font-medium text-gray-500">{t("quickActions")}</h2>
          <div className="mt-3 space-y-2">
            <Link
              to={`${ROUTES.REP}/orders/new`}
              className="block rounded-md bg-blue-600 px-4 py-2 text-center text-sm font-medium text-white hover:bg-blue-700"
            >
              {t("common:common.newOrder")}
            </Link>
            <Link
              to={`${ROUTES.REP}/customers`}
              className="block rounded-md border border-gray-300 px-4 py-2 text-center text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              {t("common:common.viewCustomers")}
            </Link>
          </div>
        </div>
      </div>

      {/* Recent Orders */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">{t("recentOrders")}</h2>
          <Link
            to={`${ROUTES.REP}/orders`}
            className="text-sm text-blue-600 hover:underline"
          >
            {t("common:common.viewAll")}
          </Link>
        </div>

        {ordersLoading ? (
          <p className="text-gray-500">{t("loading")}</p>
        ) : (recentOrders ?? []).length === 0 ? (
          <p className="text-gray-500">{t("noOrdersYet")}</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("orderNumber")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("state")}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("grandTotal")}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    {t("ordered")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {recentOrders!.map((order) => (
                  <tr key={order.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <Link
                        to={`${ROUTES.REP}/orders/${order.id}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {order.order_number}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                          STATE_BADGE[order.state] ?? "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {t(`common:orderStates.${order.state}`, { ns: "common" })}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-900">
                      {formatCurrency(order.grand_total)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {formatDate(order.ordered_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
