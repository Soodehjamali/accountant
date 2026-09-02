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
    user: { id: "u1", username: "test", email: "test@test.com", status: "ACTIVE", portal: "office" },
    permissions: new Set(["INVOICE_MANAGE", "PAYMENT_MANAGE"]),
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

const MOCK_INVOICE = {
  id: "inv-001",
  invoice_number: "INV-TEST-001",
  customer_id: "cust-123",
  currency_id: "cur-1",
  state: "ISSUED",
  subtotal: "500",
  tax_total: "0",
  discount_total: "0",
  grand_total: "500",
  amount_paid: "200",
  balance_due: "300",
  issued_at: "2026-01-15T00:00:00Z",
  due_at: "2026-02-14T00:00:00Z",
  closed_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-15T00:00:00Z",
  lines: [
    {
      id: "line-1",
      invoice_id: "inv-001",
      order_line_id: "ol-1",
      product_id: "prod-1",
      description: "Test Product",
      qty: "2",
      unit_price: "250",
      tax_rate: "0",
      tax_amount: "0",
      discount_value: "0",
      line_total: "500",
    },
  ],
};

const MOCK_PAYMENT = {
  id: "pay-001",
  payment_number: "PAY-TEST-001",
  customer_id: "cust-123",
  currency_id: "cur-1",
  received_by: "u1",
  amount: "200",
  method: "CASH",
  reference: "REF-001",
  received_at: "2026-01-20T00:00:00Z",
  unallocated_amount: "0",
  created_at: "2026-01-20T00:00:00Z",
  allocations: [
    {
      id: "alloc-1",
      payment_id: "pay-001",
      invoice_id: "inv-001",
      allocated_amount: "200",
      allocated_at: "2026-01-20T00:00:00Z",
    },
  ],
};

// Mock at hook level for reliability
vi.mock("@/api/hooks/useInvoices", () => ({
  useInvoices: () => ({ data: [], isLoading: false, error: null }),
  useInvoice: (_id: string) => ({ data: MOCK_INVOICE, isLoading: false, error: null, refetch: vi.fn() }),
  useInvoiceHistory: (_id: string) => ({ data: [], isLoading: false, error: null }),
  useInvoicePayments: (_id: string) => ({ data: [], isLoading: false, error: null }),
  useIssueInvoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useVoidInvoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/usePayments", () => ({
  usePayment: (_id: string) => ({ data: MOCK_PAYMENT, isLoading: false, error: null }),
  useRecordPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// Mock API client for list page
const mockGet = vi.fn().mockResolvedValue({ data: { items: [] }, error: null });
vi.mock("@/api/client", () => ({
  apiClient: { GET: mockGet, POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  authHeader: vi.fn(() => ({})),
  getToken: vi.fn(() => "test-token"),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));

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
// Tests
// ---------------------------------------------------------------------------

describe("InvoiceListPage", () => {
  it("renders heading and state filter", async () => {
    const { InvoiceListPage } = await import("./InvoiceListPage");
    renderWithProviders(<MemoryRouter><InvoiceListPage /></MemoryRouter>);
    expect(screen.getByText("Invoices")).toBeInTheDocument();
    expect(screen.getByText("All states")).toBeInTheDocument();
  });

  it("shows empty state", async () => {
    const { InvoiceListPage } = await import("./InvoiceListPage");
    renderWithProviders(<MemoryRouter><InvoiceListPage /></MemoryRouter>);
    expect(await screen.findByText("No invoices found.")).toBeInTheDocument();
  });
});

describe("InvoiceDetailPage", () => {
  it("renders invoice header, lines, and history", async () => {
    const { InvoiceDetailPage } = await import("./InvoiceDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/invoices/inv-001"]}>
        <InvoiceDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Invoice INV-TEST-001")).toBeInTheDocument();
    expect(screen.getByText("ISSUED")).toBeInTheDocument();
    expect(screen.getByText("Line Items")).toBeInTheDocument();
    expect(screen.getByText("Test Product")).toBeInTheDocument();
    expect(screen.getByText("Status History")).toBeInTheDocument();
  });

  it("renders payment history section for ISSUED invoices", async () => {
    const { InvoiceDetailPage } = await import("./InvoiceDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/invoices/inv-001"]}>
        <InvoiceDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Payments")).toBeInTheDocument();
  });

  it("shows Record Payment button for ISSUED invoices", async () => {
    const { InvoiceDetailPage } = await import("./InvoiceDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/invoices/inv-001"]}>
        <InvoiceDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Record Payment")).toBeInTheDocument();
  });
});

describe("PaymentDetailPage", () => {
  it("renders payment details and allocations", async () => {
    const { PaymentDetailPage } = await import("../payments/PaymentDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/payments/pay-001"]}>
        <PaymentDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Payment PAY-TEST-001")).toBeInTheDocument();
    expect(screen.getByText("CASH")).toBeInTheDocument();
    expect(screen.getByText("REF-001")).toBeInTheDocument();
    expect(screen.getByText("Allocations")).toBeInTheDocument();
  });
});
