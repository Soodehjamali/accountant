import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { describe, it, expect, vi, beforeEach } from "vitest";
import i18n from "@/i18n";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/features/auth/AuthContext", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", username: "test", email: "test@test.com", status: "ACTIVE", portal: "office" },
    permissions: new Set(["ORDER_MANAGE", "ORDER_APPROVE"]),
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

const MOCK_ORDER = {
  id: "test-order-id",
  order_number: "ORD-TEST-001",
  customer_id: "cust-123",
  representative_id: "rep-456",
  state: "DRAFT",
  order_type: "LOCAL" as const,
  fulfillment_mode: "REP_LOCAL" as const,
  sales_channel: "OFFICE",
  currency_id: "cur-1",
  price_list_id: "pl-1",
  subtotal: "100",
  discount_total: "0",
  tax_total: "0",
  grand_total: "100",
  ordered_at: "2026-01-01T00:00:00Z",
  shipped_at: null,
  invoiced_at: null,
  paid_at: null,
  customer_city_ref_id: null,
  rep_city_ref_id: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  lines: [],
  fulfillment_warehouse_id: null,
};

const mockShipMutate = vi.fn().mockResolvedValue({ state: "SHIPPED" });
const mockPayMutate = vi.fn().mockResolvedValue({ state: "PAID" });

vi.mock("@/api/hooks/useOrders", () => ({
  useOrders: () => ({ data: [], isLoading: false, error: null }),
  useOrder: (_id: string) => ({ data: MOCK_ORDER, isLoading: false, error: null }),
  useOrderHistory: (_id: string) => ({ data: [], isLoading: false, error: null }),
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
  useShipOrder: (_id: string) => ({ mutateAsync: mockShipMutate, isPending: false }),
  useMarkPaid: (_id: string) => ({ mutateAsync: mockPayMutate, isPending: false }),
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
// ADR-004 Transition Lookup Table — highest-risk logic
// ---------------------------------------------------------------------------

import { ALLOWED_TRANSITIONS } from "./OrderTransitionActions";

describe("ALLOWED_TRANSITIONS (ADR-004 state machine)", () => {
  const ALL_STATES = [
    "DRAFT", "PENDING_APPROVAL", "APPROVED", "RESERVED", "FULFILLING",
    "SHIPPED", "INVOICED", "PAID", "COMPLETED", "CANCELLED",
    "BACKORDERED", "PARTIALLY_FULFILLED", "RETURNED",
  ] as const;

  it("covers all 13 OrderState values", () => {
    expect(Object.keys(ALLOWED_TRANSITIONS).sort()).toEqual([...ALL_STATES].sort());
  });

  it("DRAFT -> PENDING_APPROVAL or CANCELLED only", () => {
    expect(ALLOWED_TRANSITIONS.DRAFT).toHaveLength(2);
    expect(ALLOWED_TRANSITIONS.DRAFT).toEqual(expect.arrayContaining(["PENDING_APPROVAL", "CANCELLED"]));
  });

  it("PENDING_APPROVAL -> APPROVED or CANCELLED only", () => {
    expect(ALLOWED_TRANSITIONS.PENDING_APPROVAL).toHaveLength(2);
    expect(ALLOWED_TRANSITIONS.PENDING_APPROVAL).toEqual(expect.arrayContaining(["APPROVED", "CANCELLED"]));
  });

  it("APPROVED -> RESERVED, BACKORDERED, or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS.APPROVED).toHaveLength(3);
    expect(ALLOWED_TRANSITIONS.APPROVED).toEqual(expect.arrayContaining(["RESERVED", "BACKORDERED", "CANCELLED"]));
  });

  it("RESERVED -> FULFILLING or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS.RESERVED).toHaveLength(2);
    expect(ALLOWED_TRANSITIONS.RESERVED).toEqual(expect.arrayContaining(["FULFILLING", "CANCELLED"]));
  });

  it("BACKORDERED -> PENDING_APPROVAL or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS.BACKORDERED).toHaveLength(2);
    expect(ALLOWED_TRANSITIONS.BACKORDERED).toEqual(expect.arrayContaining(["PENDING_APPROVAL", "CANCELLED"]));
  });

  it("FULFILLING -> SHIPPED, PARTIALLY_FULFILLED, or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS.FULFILLING).toHaveLength(3);
    expect(ALLOWED_TRANSITIONS.FULFILLING).toEqual(expect.arrayContaining(["SHIPPED", "PARTIALLY_FULFILLED", "CANCELLED"]));
  });

  it("PARTIALLY_FULFILLED -> SHIPPED, RETURNED, or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS.PARTIALLY_FULFILLED).toHaveLength(3);
    expect(ALLOWED_TRANSITIONS.PARTIALLY_FULFILLED).toEqual(expect.arrayContaining(["SHIPPED", "RETURNED", "CANCELLED"]));
  });

  it("SHIPPED -> INVOICED or RETURNED", () => {
    expect(ALLOWED_TRANSITIONS.SHIPPED).toHaveLength(2);
    expect(ALLOWED_TRANSITIONS.SHIPPED).toEqual(expect.arrayContaining(["INVOICED", "RETURNED"]));
  });

  it("INVOICED -> PAID only", () => {
    expect(ALLOWED_TRANSITIONS.INVOICED).toEqual(["PAID"]);
  });

  it("PAID -> COMPLETED only", () => {
    expect(ALLOWED_TRANSITIONS.PAID).toEqual(["COMPLETED"]);
  });

  it("COMPLETED, CANCELLED, RETURNED are terminal", () => {
    expect(ALLOWED_TRANSITIONS.COMPLETED).toEqual([]);
    expect(ALLOWED_TRANSITIONS.CANCELLED).toEqual([]);
    expect(ALLOWED_TRANSITIONS.RETURNED).toEqual([]);
  });

  it("all target states are valid OrderState values", () => {
    const stateSet = new Set(ALL_STATES);
    for (const [from, targets] of Object.entries(ALLOWED_TRANSITIONS)) {
      expect(stateSet.has(from as any)).toBe(true);
      for (const target of targets) {
        expect(stateSet.has(target as any)).toBe(true);
      }
    }
  });

  it("cancel reachable from all pre-SHIPPED states", () => {
    for (const state of ["DRAFT", "PENDING_APPROVAL", "APPROVED", "RESERVED", "BACKORDERED", "FULFILLING", "PARTIALLY_FULFILLED"]) {
      expect(ALLOWED_TRANSITIONS[state as keyof typeof ALLOWED_TRANSITIONS]).toContain("CANCELLED");
    }
  });

  it("return reachable from SHIPPED and PARTIALLY_FULFILLED only", () => {
    for (const state of ALL_STATES) {
      const canReturn = ALLOWED_TRANSITIONS[state].includes("RETURNED");
      expect(canReturn).toBe(state === "SHIPPED" || state === "PARTIALLY_FULFILLED");
    }
  });
});

// ---------------------------------------------------------------------------
// Component smoke tests
// ---------------------------------------------------------------------------

describe("OrderListPage", () => {
  it("renders heading and state filter", async () => {
    const { OrderListPage } = await import("./OrderListPage");
    renderWithProviders(<MemoryRouter><OrderListPage /></MemoryRouter>);
    expect(screen.getByText("Orders")).toBeInTheDocument();
    expect(screen.getByText("All states")).toBeInTheDocument();
  });

  it("shows empty state when no orders", async () => {
    const { OrderListPage } = await import("./OrderListPage");
    renderWithProviders(<MemoryRouter><OrderListPage /></MemoryRouter>);
    expect(await screen.findByText("No orders found.")).toBeInTheDocument();
  });
});

describe("OrderDetailPage", () => {
  it("renders order header, lines, and history", async () => {
    const { OrderDetailPage } = await import("./OrderDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/orders/test-order-id"]}>
        <OrderDetailPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Order ORD-TEST-001")).toBeInTheDocument();
    expect(screen.getByText("DRAFT")).toBeInTheDocument();
    expect(screen.getByText("Line Items")).toBeInTheDocument();
    expect(screen.getByText("Status History")).toBeInTheDocument();
  });

  it("does not show invoice link for DRAFT orders", async () => {
    const { OrderDetailPage } = await import("./OrderDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/orders/test-order-id"]}>
        <OrderDetailPage />
      </MemoryRouter>,
    );
    await screen.findByText("Order ORD-TEST-001");
    // DRAFT order should NOT show "View Invoice"
    expect(screen.queryByText("View Invoice")).not.toBeInTheDocument();
  });
});

describe("OrderCreatePage", () => {
  it("renders form fields and line item entry", async () => {
    const { OrderCreatePage } = await import("./OrderCreatePage");
    renderWithProviders(<MemoryRouter><OrderCreatePage /></MemoryRouter>);
    expect(screen.getAllByText("Create Order").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText(/Customer ID/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Representative ID/)).toBeInTheDocument();
    expect(screen.getByText("Line Items")).toBeInTheDocument();
    expect(screen.getByText("+ Add Line")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Ship and Pay dialog tests
// ---------------------------------------------------------------------------

const ORDER_WITH_LINES = {
  ...MOCK_ORDER,
  state: "FULFILLING" as const,
  lines: [
    {
      id: "line-1",
      product_id: "prod-aaa",
      lot_id: null,
      fulfillment_warehouse_id: "wh-1",
      qty_ordered: "10",
      qty_reserved: "10",
      qty_shipped: "0",
      qty_returned: "0",
      unit_price: "25.50",
      discount_value: "0",
      discount_id: null,
      price_history_id: "ph-1",
      line_total: "255.00",
      fulfillment_mode: "REP_LOCAL" as const,
    },
    {
      id: "line-2",
      product_id: "prod-bbb",
      lot_id: null,
      fulfillment_warehouse_id: "wh-1",
      qty_ordered: "5",
      qty_reserved: "5",
      qty_shipped: "3",
      qty_returned: "0",
      unit_price: "10.00",
      discount_value: "0",
      discount_id: null,
      price_history_id: "ph-2",
      line_total: "50.00",
      fulfillment_mode: "REP_LOCAL" as const,
    },
  ],
};

describe("ShipDialog", () => {
  beforeEach(() => {
    mockShipMutate.mockReset();
    mockShipMutate.mockResolvedValue({ state: "SHIPPED" });
  });

  it("shows ship dialog with unshipped lines and editable quantities", async () => {
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_WITH_LINES} />
      </MemoryRouter>,
    );

    // Click the Ship button
    const shipBtn = screen.getByText("Ship");
    await shipBtn.click();

    // Dialog should appear with line details
    expect(screen.getByText(/Ship Order ORD-TEST-001/)).toBeInTheDocument();
    expect(screen.getByText("prod-aaa")).toBeInTheDocument();
    expect(screen.getByText("prod-bbb")).toBeInTheDocument();

    // line-1: remaining = 10 - 0 = 10 (fully unshipped)
    // line-2: remaining = 5 - 3 = 2 (partially shipped)
    const inputs = screen.getAllByRole("spinbutton");
    expect(inputs).toHaveLength(2);
  });

  it("submits the expected lines payload", async () => {
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_WITH_LINES} />
      </MemoryRouter>,
    );

    await screen.getByText("Ship").click();
    await screen.getByText("Confirm Ship").click();

    expect(mockShipMutate).toHaveBeenCalledTimes(1);
    const submittedLines = mockShipMutate.mock.calls[0][0];
    expect(submittedLines).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          order_line_id: "line-1",
          quantity: "10",
        }),
        expect.objectContaining({
          order_line_id: "line-2",
          quantity: "2",
        }),
      ]),
    );
  });

  it("surfaces a mocked 422 error as inline error", async () => {
    mockShipMutate.mockRejectedValueOnce(
      new Error("422: Shipment quantity exceeds reserved quantity."),
    );
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_WITH_LINES} />
      </MemoryRouter>,
    );

    await screen.getByText("Ship").click();
    await screen.getByText("Confirm Ship").click();

    expect(
      await screen.findByText(/422: Shipment quantity exceeds/),
    ).toBeInTheDocument();
  });
});

describe("PayDialog", () => {
  const ORDER_INVOICED = {
    ...MOCK_ORDER,
    state: "INVOICED" as const,
    grand_total: "305.00",
    lines: [],
  };

  beforeEach(() => {
    mockPayMutate.mockReset();
    mockPayMutate.mockResolvedValue({ state: "PAID" });
  });

  it("shows pay dialog with amount, method, reference, note fields", async () => {
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_INVOICED} />
      </MemoryRouter>,
    );

    await screen.getByText("Mark Paid").click();

    expect(screen.getByText(/Record Payment/)).toBeInTheDocument();
    expect(screen.getByText(/ORD-TEST-001/)).toBeInTheDocument();
    // Amount pre-filled with grand_total
    const amountInput = screen.getByDisplayValue("305.00");
    expect(amountInput).toBeInTheDocument();
    // Method defaults to CASH
    expect(screen.getByDisplayValue("Cash")).toBeInTheDocument();
    // Reference and note are empty
    expect(screen.getByPlaceholderText(/check number/)).toBeInTheDocument();
  });

  it("submits the expected amount/method payload", async () => {
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_INVOICED} />
      </MemoryRouter>,
    );

    await screen.getByText("Mark Paid").click();
    await screen.getByText("Confirm Payment").click();

    expect(mockPayMutate).toHaveBeenCalledTimes(1);
    const submittedBody = mockPayMutate.mock.calls[0][0];
    expect(submittedBody).toMatchObject({
      amount: "305.00",
      method: "CASH",
      reference: null,
      note: null,
    });
  });

  it("surfaces a mocked 422 error as inline error", async () => {
    mockPayMutate.mockRejectedValueOnce(
      new Error("422: Payment amount is less than the invoice balance due."),
    );
    const { OrderTransitionActions } = await import("./OrderTransitionActions");
    renderWithProviders(
      <MemoryRouter>
        <OrderTransitionActions order={ORDER_INVOICED} />
      </MemoryRouter>,
    );

    await screen.getByText("Mark Paid").click();
    await screen.getByText("Confirm Payment").click();

    expect(
      await screen.findByText(/422: Payment amount is less/),
    ).toBeInTheDocument();
  });
});
