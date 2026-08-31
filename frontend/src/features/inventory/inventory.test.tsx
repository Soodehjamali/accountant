import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/api/hooks/useWarehouses", () => ({
  useWarehouses: vi.fn(() => ({
    data: [
      { id: "wh-1", code: "MAIN", name: "Main Warehouse" },
      { id: "wh-2", code: "BRANCH", name: "Branch Warehouse" },
    ],
  })),
}));

vi.mock("@/api/hooks/useProducts", () => ({
  useProducts: vi.fn(() => ({
    data: [
      { id: "p-1", sku: "WIDGET-001", name: "Widget" },
      { id: "p-2", sku: "GADGET-001", name: "Gadget" },
    ],
  })),
}));

vi.mock("@/api/hooks/useInventory", () => ({
  useInventoryTransactions: vi.fn(() => ({
    data: [
      {
        id: "txn-1",
        sequence_no: 2,
        movement_type_id: "mt-1",
        signed_quantity: "50",
        unit_cost: "10.00",
        reference_type: null,
        reference_id: null,
        is_reversed: false,
        occurred_at: "2026-08-30T10:00:00Z",
        warehouse_id: "wh-1",
        product_id: "p-1",
      },
      {
        id: "txn-2",
        sequence_no: 1,
        movement_type_id: "mt-2",
        signed_quantity: "-10",
        unit_cost: "10.00",
        reference_type: "order",
        reference_id: "ord-1",
        is_reversed: false,
        occurred_at: "2026-08-30T11:00:00Z",
        warehouse_id: "wh-1",
        product_id: "p-1",
      },
    ],
    isLoading: false,
  })),
  useInventoryBalance: vi.fn(() => ({
    data: { balance: "40.0000", warehouse_id: "wh-1", product_id: "p-1", lot_id: null },
  })),
  usePostTransaction: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useReverseTransaction: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useMovementTypes: vi.fn(() => ({
    data: [
      { code: "RECEIPT_FROM_PRODUCTION", label: "Receipt from production", sign: 1 },
      { code: "SALE_OUT", label: "Sale", sign: -1 },
      { code: "REVERSAL", label: "System reversal", sign: 1 },
    ],
  })),
}));

vi.mock("@/api/hooks/useReasonCodes", () => ({
  useReasonCodes: vi.fn(() => ({
    data: [
      { id: "rc-1", code: "PRICING_ERROR", label: "Pricing error", scope: "ADJUSTMENT" },
    ],
  })),
}));

vi.mock("@/hooks/usePermission", () => ({
  usePermission: vi.fn(() => true),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

import { InventoryLedgerPage } from "./InventoryLedgerPage";

describe("InventoryLedgerPage", () => {
  it("renders the page heading", () => {
    render(<InventoryLedgerPage />, { wrapper });
    expect(screen.getByText("Inventory Ledger")).toBeInTheDocument();
  });

  it("explains that balances are computed live", () => {
    render(<InventoryLedgerPage />, { wrapper });
    expect(
      screen.getByText(/Balances are always computed live/),
    ).toBeInTheDocument();
  });

  it("renders warehouse and product selectors", () => {
    render(<InventoryLedgerPage />, { wrapper });
    expect(screen.getByText("Warehouse *")).toBeInTheDocument();
    expect(screen.getByText("Product *")).toBeInTheDocument();
  });

  it("does NOT show Post Transaction form without warehouse+product selected", () => {
    render(<InventoryLedgerPage />, { wrapper });
    expect(screen.queryByText("Post Transaction")).not.toBeInTheDocument();
  });
});
