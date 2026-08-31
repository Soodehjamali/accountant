import "@/i18n";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/features/auth/AuthContext";
import { LoginPage } from "@/features/auth/LoginPage";
import { AppShell } from "@/features/layout/AppShell";
import { ProductListPage } from "@/features/catalog/ProductListPage";
import { ProductDetailPage } from "@/features/catalog/ProductDetailPage";
import { ProductCreatePage } from "@/features/catalog/ProductCreatePage";
import { CustomerListPage } from "@/features/customers/CustomerListPage";
import { CustomerDetailPage } from "@/features/customers/CustomerDetailPage";
import { CustomerCreatePage } from "@/features/customers/CustomerCreatePage";
import { RepCustomerListPage } from "@/features/customers/RepCustomerListPage";
import { RepCustomerDetailPage } from "@/features/customers/RepCustomerDetailPage";
import { OrderListPage } from "@/features/orders/OrderListPage";
import { OrderDetailPage } from "@/features/orders/OrderDetailPage";
import { OrderCreatePage } from "@/features/orders/OrderCreatePage";
import { RepOrderListPage } from "@/features/orders/RepOrderListPage";
import { RepOrderCreatePage } from "@/features/orders/RepOrderCreatePage";
import { RepOrderDetailPage } from "@/features/orders/RepOrderDetailPage";
import { InvoiceListPage } from "@/features/invoices/InvoiceListPage";
import { InvoiceDetailPage } from "@/features/invoices/InvoiceDetailPage";
import { PaymentDetailPage } from "@/features/payments/PaymentDetailPage";
import { CreditNoteCreatePage } from "@/features/credit-notes/CreditNoteCreatePage";
import { CreditNoteDetailPage } from "@/features/credit-notes/CreditNoteDetailPage";
import { TransferListPage } from "@/features/transfers/TransferListPage";
import { TransferDetailPage } from "@/features/transfers/TransferDetailPage";
import { TransferCreatePage } from "@/features/transfers/TransferCreatePage";
import { InventoryLedgerPage } from "@/features/inventory/InventoryLedgerPage";
import { RepInventoryPage } from "@/features/inventory/RepInventoryPage";
import { ReportListPage } from "@/features/reports/ReportListPage";
import { ReportRunDetailPage } from "@/features/reports/ReportRunDetailPage";
import { KpiDashboardPage } from "@/features/kpi/KpiDashboardPage";
import { RepDashboardPage } from "@/features/dashboard/RepDashboardPage";
import { RepCommissionPage } from "@/features/commissions/RepCommissionPage";
import { ROUTES } from "@/lib/constants";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

/** Redirect to office or rep portal based on user role. */
function HomeRedirect() {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-gray-500">Loading…</p>
      </div>
    );
  }

  if (!user) {
    return <Navigate to={ROUTES.LOGIN} replace />;
  }

  return (
    <Navigate
      to={
        user.portal === "representative"
          ? ROUTES.REP
          : `${ROUTES.OFFICE}/kpi`
      }
      replace
    />
  );
}

/** Placeholder page for routes not yet implemented. */
function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-900">{title}</h2>
      <p className="mt-2 text-gray-500">
        This page is part of a future milestone.
      </p>
    </div>
  );
}

function AppRoutes() {
  return (
    <Routes>
      {/* Public routes */}
      <Route path={ROUTES.LOGIN} element={<LoginPage />} />

      {/* Home redirect */}
      <Route path={ROUTES.HOME} element={<HomeRedirect />} />

      {/* Office/admin shell */}
      <Route path={ROUTES.OFFICE} element={<AppShell />}>
        <Route index element={<PlaceholderPage title="Office Dashboard" />} />
        <Route path="dashboard" element={<PlaceholderPage title="Office Dashboard" />} />
        {/* Catalog */}
        <Route path="catalog" element={<ProductListPage />} />
        <Route path="catalog/new" element={<ProductCreatePage />} />
        <Route path="catalog/:sku" element={<ProductDetailPage />} />
        {/* Customers */}
        <Route path="customers" element={<CustomerListPage />} />
        <Route path="customers/new" element={<CustomerCreatePage />} />
        <Route path="customers/:id" element={<CustomerDetailPage />} />
        {/* Orders */}
        <Route path="orders" element={<OrderListPage />} />
        <Route path="orders/new" element={<OrderCreatePage />} />
        <Route path="orders/:id" element={<OrderDetailPage />} />
        {/* Invoices */}
        <Route path="invoices" element={<InvoiceListPage />} />
        <Route path="invoices/:id" element={<InvoiceDetailPage />} />
        {/* Payments (reached from invoice/order detail) */}
        <Route path="payments/:id" element={<PaymentDetailPage />} />
        {/* Credit Notes (reached from invoice detail) */}
        <Route path="credit-notes/new" element={<CreditNoteCreatePage />} />
        <Route path="credit-notes/:id" element={<CreditNoteDetailPage />} />
        {/* Inventory */}
        <Route path="inventory" element={<InventoryLedgerPage />} />
        {/* Transfers */}
        <Route path="transfers" element={<TransferListPage />} />
        <Route path="transfers/new" element={<TransferCreatePage />} />
        <Route path="transfers/:id" element={<TransferDetailPage />} />
        <Route path="payments" element={<PlaceholderPage title="Payments" />} />
        <Route path="commissions" element={<PlaceholderPage title="Commissions" />} />
        <Route path="reports" element={<ReportListPage />} />
        <Route path="reports/runs/:id" element={<ReportRunDetailPage />} />
        <Route path="kpi" element={<KpiDashboardPage />} />
        <Route path="audit-log" element={<PlaceholderPage title="Audit Log" />} />
      </Route>

      {/* Representative portal shell */}
      <Route path={ROUTES.REP} element={<AppShell />}>
        <Route index element={<RepDashboardPage />} />
        <Route path="dashboard" element={<RepDashboardPage />} />
        {/* Customers (read-only) */}
        <Route path="customers" element={<RepCustomerListPage />} />
        <Route path="customers/:id" element={<RepCustomerDetailPage />} />
        {/* Orders — list/create/detail reuse office components with rep routing */}
        <Route path="orders" element={<RepOrderListPage />} />
        <Route path="orders/new" element={<RepOrderCreatePage />} />
        <Route path="orders/:id" element={<RepOrderDetailPage />} />
        {/* Inventory (read-only warehouse balance) */}
        <Route path="inventory" element={<RepInventoryPage />} />
        {/* Commission (balance + transaction history) */}
        <Route path="commission" element={<RepCommissionPage />} />
      </Route>

      {/* Catch-all: redirect to home */}
      <Route path="*" element={<Navigate to={ROUTES.HOME} replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
