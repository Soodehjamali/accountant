/**
 * Representative Portal tests — Vitest + RTL.
 *
 * Covers:
 * 1. AppShell: rep nav shows correct items (Dashboard, My Customers,
 *    My Orders, My Inventory, My Commission) and hides office items.
 * 2. RepDashboardPage: renders commission balance card and order summary.
 * 3. RepCustomerListPage: renders read-only list with no action buttons.
 * 4. RepInventoryPage: renders warehouse selector.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { describe, it, expect, vi } from "vitest";
import i18n from "@/i18n";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/auth/AuthContext", () => ({
  useAuth: () => ({
    token: "test-token",
    user: {
      id: "u1",
      username: "rep-user",
      email: "rep@test.com",
      status: "ACTIVE",
      portal: "representative",
    },
    permissions: new Set<string>(),
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/api/client", () => ({
  apiClient: { GET: vi.fn(), POST: vi.fn() },
  authHeader: vi.fn(() => ({})),
  getToken: vi.fn(() => "test-token"),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));

vi.mock("@/api/hooks/useOrders", () => ({
  useOrders: () => ({ data: [], isLoading: false, error: null }),
  useOrder: () => ({ data: null, isLoading: false, error: null }),
  useOrderHistory: () => ({ data: [], isLoading: false, error: null }),
  useCreateOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAddOrderLine: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRemoveOrderLine: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateOrderLineQty: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateOrderLinePrice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSubmitOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApproveOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReserveOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResubmitOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useStartFulfillment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRecordReturn: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMarkInvoiced: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMarkCompleted: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useShipOrder: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMarkPaid: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/useCustomers", () => ({
  useCustomers: () => ({ data: [], isLoading: false, error: null }),
  useCustomer: () => ({ data: null, isLoading: false, error: null }),
  useCreateCustomer: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/useCommissions", () => ({
  useCommissionBalance: () => ({
    data: { representative_id: "rep-1", balance: "1250.00" },
    isLoading: false,
  }),
  useCommissionTransactions: () => ({
    data: [],
    isLoading: false,
  }),
}));

vi.mock("@/api/hooks/useInvoices", () => ({
  useInvoices: () => ({ data: [], isLoading: false, error: null }),
}));

// Mock the warehouse query used by RepInventoryPage
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: (options: any) => {
      // For "warehouses/my" query
      if (options.queryKey?.[0] === "warehouses" && options.queryKey?.[1] === "my") {
        return {
          data: [
            { id: "wh-1", code: "MAIN", name: "Main Warehouse", type: "FACTORY", status: "ACTIVE" },
          ],
          isLoading: false,
        };
      }
      // For "inventory-balance" query
      if (options.queryKey?.[0] === "inventory-balance") {
        return { data: null, isLoading: false };
      }
      // Default: return empty
      return { data: undefined, isLoading: false };
    },
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        {ui}
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

// ---------------------------------------------------------------------------
// AppShell — Rep nav items
// ---------------------------------------------------------------------------

describe("AppShell — Rep Portal Nav", () => {
  it("renders rep-specific nav items", async () => {
    const { AppShell } = await import("@/features/layout/AppShell");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/dashboard"]}>
        <AppShell />
      </MemoryRouter>,
    );

    expect(screen.getByText("Rep Portal")).toBeInTheDocument();
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("My Customers")).toBeInTheDocument();
    expect(screen.getByText("My Orders")).toBeInTheDocument();
    expect(screen.getByText("My Inventory")).toBeInTheDocument();
    expect(screen.getByText("My Commission")).toBeInTheDocument();
  });

  it("does not render office-specific nav items", async () => {
    const { AppShell } = await import("@/features/layout/AppShell");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/dashboard"]}>
        <AppShell />
      </MemoryRouter>,
    );

    expect(screen.queryByText("Products")).not.toBeInTheDocument();
    expect(screen.queryByText("Invoices")).not.toBeInTheDocument();
    expect(screen.queryByText("Transfers")).not.toBeInTheDocument();
    expect(screen.queryByText("Payments")).not.toBeInTheDocument();
    expect(screen.queryByText("KPI Dashboard")).not.toBeInTheDocument();
    expect(screen.queryByText("Reports")).not.toBeInTheDocument();
    expect(screen.queryByText("Audit Log")).not.toBeInTheDocument();
  });

  it("renders the sign out button", async () => {
    const { AppShell } = await import("@/features/layout/AppShell");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/dashboard"]}>
        <AppShell />
      </MemoryRouter>,
    );

    expect(screen.getByText("Sign out")).toBeInTheDocument();
    expect(screen.getByText("rep-user")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RepDashboardPage
// ---------------------------------------------------------------------------

describe("RepDashboardPage", () => {
  it("renders the dashboard heading", async () => {
    const { RepDashboardPage } = await import("@/features/dashboard/RepDashboardPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep"]}>
        <RepDashboardPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Representative Dashboard")).toBeInTheDocument();
  });

  it("renders the commission balance card", async () => {
    const { RepDashboardPage } = await import("@/features/dashboard/RepDashboardPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep"]}>
        <RepDashboardPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Commission Balance")).toBeInTheDocument();
    // Balance from mock: "1250.00" — formatCurrency renders 2 decimal places
    expect(screen.getByText("1,250.00")).toBeInTheDocument();
  });

  it("renders the active orders count card", async () => {
    const { RepDashboardPage } = await import("@/features/dashboard/RepDashboardPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep"]}>
        <RepDashboardPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Active Orders")).toBeInTheDocument();
  });

  it("renders quick action buttons", async () => {
    const { RepDashboardPage } = await import("@/features/dashboard/RepDashboardPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep"]}>
        <RepDashboardPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("New Order")).toBeInTheDocument();
    expect(screen.getByText("View Customers")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RepCustomerListPage
// ---------------------------------------------------------------------------

describe("RepCustomerListPage", () => {
  it("renders the heading", async () => {
    const { RepCustomerListPage } = await import("@/features/customers/RepCustomerListPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/customers"]}>
        <RepCustomerListPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("My Customers")).toBeInTheDocument();
  });

  it("renders search input and status filter", async () => {
    const { RepCustomerListPage } = await import("@/features/customers/RepCustomerListPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/customers"]}>
        <RepCustomerListPage />
      </MemoryRouter>,
    );

    expect(screen.getByPlaceholderText(/Search by name/)).toBeInTheDocument();
    expect(screen.getByText("All statuses")).toBeInTheDocument();
  });

  it("does not render create/edit/deactivate buttons", async () => {
    const { RepCustomerListPage } = await import("@/features/customers/RepCustomerListPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/customers"]}>
        <RepCustomerListPage />
      </MemoryRouter>,
    );

    expect(screen.queryByText("New Customer")).not.toBeInTheDocument();
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
    expect(screen.queryByText("Deactivate")).not.toBeInTheDocument();
  });

  it("shows empty state when no customers", async () => {
    const { RepCustomerListPage } = await import("@/features/customers/RepCustomerListPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/customers"]}>
        <RepCustomerListPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("No customers found.")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// RepInventoryPage
// ---------------------------------------------------------------------------

describe("RepInventoryPage", () => {
  it("renders the heading", async () => {
    const { RepInventoryPage } = await import("@/features/inventory/RepInventoryPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/inventory"]}>
        <RepInventoryPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("My Inventory")).toBeInTheDocument();
  });

  it("renders the assigned warehouses section", async () => {
    const { RepInventoryPage } = await import("@/features/inventory/RepInventoryPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/rep/inventory"]}>
        <RepInventoryPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Assigned Warehouses")).toBeInTheDocument();
    expect(screen.getByText("Main Warehouse")).toBeInTheDocument();
    expect(screen.getByText("MAIN")).toBeInTheDocument();
  });
});
