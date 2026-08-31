import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/auth/AuthContext", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", username: "test", email: "test@test.com", status: "ACTIVE", portal: "office" },
    permissions: new Set(["CREDIT_NOTE_MANAGE", "INVOICE_MANAGE"]),
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

const MOCK_CREDIT_NOTE = {
  id: "cn-001",
  credit_note_number: "CN-TEST-001",
  invoice_id: "inv-001",
  customer_id: "cust-123",
  issued_by: "u1",
  reason_code_id: "rc-001",
  reference_type: null,
  reference_id: null,
  total_amount: "100",
  state: "DRAFT",
  issued_at: null,
  created_at: "2026-01-01T00:00:00Z",
  lines: [
    {
      id: "line-1",
      credit_note_id: "cn-001",
      invoice_line_id: null,
      description: "Test credit line",
      qty: "1",
      unit_price: "100",
      line_total: "100",
    },
  ],
};

const MOCK_INVOICE_WITH_CN = {
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
  lines: [],
};

vi.mock("@/api/hooks/useCreditNotes", () => ({
  useCreditNotes: ({ invoice_id }: { invoice_id?: string }) => ({
    data: invoice_id === "inv-001" ? [MOCK_CREDIT_NOTE] : [],
    isLoading: false,
    error: null,
  }),
  useCreditNote: (_id: string) => ({
    data: MOCK_CREDIT_NOTE,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateCreditNote: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useIssueCreditNote: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useApplyCreditNote: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useVoidCreditNote: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/useInvoices", () => ({
  useInvoices: () => ({ data: [], isLoading: false, error: null }),
  useInvoice: (_id: string) => ({
    data: MOCK_INVOICE_WITH_CN,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useInvoiceHistory: () => ({ data: [], isLoading: false, error: null }),
  useInvoicePayments: () => ({ data: [], isLoading: false, error: null }),
  useIssueInvoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useVoidInvoice: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/usePayments", () => ({
  useRecordPayment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/useReasonCodes", () => ({
  useReasonCodes: (_scope?: string) => ({
    data: [
      { id: "rc-001", code: "DEFECTIVE", label: "Defective product", scope: "RETURN" },
      { id: "rc-002", code: "PRICING_ERROR", label: "Pricing error", scope: "ADJUSTMENT" },
    ],
    isLoading: false,
    error: null,
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {ui}
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CreditNoteDetailPage", () => {
  it("renders credit note header, lines, and invoice link", async () => {
    const { CreditNoteDetailPage } = await import("./CreditNoteDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/credit-notes/cn-001"]}>
        <CreditNoteDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Credit Note CN-TEST-001")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText("Line Items")).toBeInTheDocument();
    expect(screen.getByText("Test credit line")).toBeInTheDocument();
    // Invoice link should be present
    expect(screen.getByText("inv-001")).toBeInTheDocument();
  });

  it("shows Issue and Void buttons for DRAFT credit notes", async () => {
    const { CreditNoteDetailPage } = await import("./CreditNoteDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/credit-notes/cn-001"]}>
        <CreditNoteDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Issue Credit Note")).toBeInTheDocument();
    expect(screen.getByText("Void Credit Note")).toBeInTheDocument();
  });
});

describe("CreditNoteCreatePage", () => {
  it("renders form with reason code dropdown and line items", async () => {
    const { CreditNoteCreatePage } = await import("./CreditNoteCreatePage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/credit-notes/new?invoice_id=inv-001"]}>
        <CreditNoteCreatePage />
      </MemoryRouter>,
    );

    const elements = await screen.findAllByText("Create Credit Note");
    expect(elements).toHaveLength(2);
    expect(screen.getByText("Reason Code *")).toBeInTheDocument();
    expect(screen.getByText("Line Items")).toBeInTheDocument();
    expect(screen.getByText("+ Add Line")).toBeInTheDocument();
  });
});

describe("InvoiceDetailPage credit notes section", () => {
  it("renders credit notes section with linked credit note", async () => {
    const { InvoiceDetailPage } = await import("../invoices/InvoiceDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/invoices/inv-001"]}>
        <InvoiceDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Credit Notes")).toBeInTheDocument();
    expect(screen.getByText("CN-TEST-001")).toBeInTheDocument();
    expect(screen.getByText("New Credit Note")).toBeInTheDocument();
  });
});
