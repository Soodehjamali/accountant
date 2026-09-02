import { Link, Outlet, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth/AuthContext";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS, ROUTES } from "@/lib/constants";
import { cn } from "@/lib/utils";

/**
 * Nav items visible to admin/office users (no representative link).
 * Each item is gated by a permission code.
 * `navKey` is the i18n key under the "common" namespace.
 */
const OFFICE_NAV_ITEMS = [
  { navKey: "nav.dashboard", path: `${ROUTES.OFFICE}/dashboard`, permission: null },
  { navKey: "nav.products", path: `${ROUTES.OFFICE}/catalog`, permission: null },
  { navKey: "nav.productCategories", path: `${ROUTES.OFFICE}/catalog/categories`, permission: PERMISSIONS.PRODUCT_MANAGE },
  { navKey: "nav.unitsOfMeasure", path: `${ROUTES.OFFICE}/catalog/uom`, permission: PERMISSIONS.PRODUCT_MANAGE },
  { navKey: "nav.warehouses", path: ROUTES.OFFICE + "/warehouses", permission: PERMISSIONS.WAREHOUSE_MANAGE },
  { navKey: "nav.representatives", path: ROUTES.OFFICE + "/representatives", permission: PERMISSIONS.REPRESENTATIVE_MANAGE },
  { navKey: "nav.customers", path: `${ROUTES.OFFICE}/customers`, permission: null },
  { navKey: "nav.orders", path: `${ROUTES.OFFICE}/orders`, permission: null },
  { navKey: "nav.invoices", path: `${ROUTES.OFFICE}/invoices`, permission: null },
  { navKey: "nav.inventory", path: `${ROUTES.OFFICE}/inventory`, permission: PERMISSIONS.INVENTORY_MANAGE },
  { navKey: "nav.returns", path: `${ROUTES.OFFICE}/returns`, permission: "RETURN_MANAGE" },
  { navKey: "nav.transfers", path: `${ROUTES.OFFICE}/transfers`, permission: PERMISSIONS.TRANSFER_MANAGE },
  { navKey: "nav.payments", path: `${ROUTES.OFFICE}/payments`, permission: PERMISSIONS.PAYMENT_MANAGE },
  { navKey: "nav.commissions", path: `${ROUTES.OFFICE}/commissions`, permission: PERMISSIONS.COMMISSION_MANAGE },
  { navKey: "nav.kpiDashboard", path: `${ROUTES.OFFICE}/kpi`, permission: PERMISSIONS.KPI_SNAPSHOT_VIEW },
  { navKey: "nav.reports", path: `${ROUTES.OFFICE}/reports`, permission: PERMISSIONS.REPORT_MANAGE },
  { navKey: "nav.auditLog", path: `${ROUTES.OFFICE}/audit-log`, permission: PERMISSIONS.AUDIT_LOG_VIEW },
];

/**
 * Nav items visible to representative-linked users.
 */
const REP_NAV_ITEMS = [
  { navKey: "nav.dashboard", path: `${ROUTES.REP}/dashboard`, permission: null },
  { navKey: "nav.myCustomers", path: `${ROUTES.REP}/customers`, permission: null },
  { navKey: "nav.myOrders", path: `${ROUTES.REP}/orders`, permission: null },
  { navKey: "nav.myInventory", path: `${ROUTES.REP}/inventory`, permission: null },
  { navKey: "nav.myCommission", path: `${ROUTES.REP}/commission`, permission: null },
];

function NavLink({
  to,
  label,
  active,
}: {
  to: string;
  label: string;
  active: boolean;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-blue-100 text-blue-700"
          : "text-gray-600 hover:bg-gray-100 hover:text-gray-900",
      )}
    >
      {label}
    </Link>
  );
}

function NavItem({
  item,
}: {
  item: (typeof OFFICE_NAV_ITEMS)[number];
}) {
  const { t } = useTranslation("common");
  const hasPermission = usePermission(item.permission ?? "");
  const location = useLocation();

  // Items without a permission requirement are always visible.
  // Items with a permission requirement are hidden if the user lacks it.
  if (item.permission && !hasPermission) {
    return null;
  }

  return (
    <NavLink
      to={item.path}
      label={t(item.navKey)}
      active={location.pathname.startsWith(item.path)}
    />
  );
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { t, i18n } = useTranslation("common");
  const isRepresentative = user?.portal === "representative";
  const navItems = isRepresentative ? REP_NAV_ITEMS : OFFICE_NAV_ITEMS;
  const shellLabel = isRepresentative ? t("shell.repPortal") : t("shell.office");

  const toggleLanguage = () => {
    const next = i18n.language === "fa" ? "en" : "fa";
    i18n.changeLanguage(next);
  };

  const otherLanguageLabel = i18n.language === "fa" ? "English" : "فارسی";

  return (
    <div className="flex min-h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col border-r border-gray-200 bg-white">
        <div className="border-b border-gray-200 px-4 py-4">
          <h1 className="text-lg font-bold text-gray-900">{t("app.name")}</h1>
          <p className="text-xs text-gray-500">{shellLabel}</p>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-4">
          {navItems.map((item) => (
            <NavItem key={item.path} item={item} />
          ))}
        </nav>

        <div className="border-t border-gray-200 px-4 py-3">
          <p className="truncate text-xs text-gray-500">{user?.username}</p>
          <div className="mt-1 flex items-center gap-2">
            <button
              onClick={toggleLanguage}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              {otherLanguageLabel}
            </button>
            <span className="text-gray-300">|</span>
            <button
              onClick={logout}
              className="text-xs text-red-600 hover:text-red-800"
            >
              {t("shell.signOut")}
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
