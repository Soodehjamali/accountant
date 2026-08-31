import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/api/hooks/useTransfers", () => ({
  useTransfers: vi.fn(() => ({
    data: [
      {
        id: "t-001",
        transfer_number: "TF-001",
        source_warehouse_id: "wh-1",
        destination_warehouse_id: "wh-2",
        state: "DRAFT",
        requested_by: "u-1",
        approved_by: null,
        requested_at: "2026-08-30T10:00:00Z",
        approved_at: null,
        dispatched_at: null,
        received_at: null,
        ownership_mode_snapshot: "OWNED",
        created_at: "2026-08-30T10:00:00Z",
        updated_at: "2026-08-30T10:00:00Z",
        lines: [
          {
            id: "line-1",
            stock_transfer_id: "t-001",
            product_id: "p-1",
            lot_id: null,
            qty_requested: "50",
            qty_dispatched: "0",
            qty_received: "0",
            unit_cost: "10.00",
            qty_variance: "0",
          },
        ],
      },
      {
        id: "t-002",
        transfer_number: "TF-002",
        source_warehouse_id: "wh-1",
        destination_warehouse_id: "wh-3",
        state: "DISPATCHED",
        requested_by: "u-1",
        approved_by: "u-2",
        requested_at: "2026-08-29T10:00:00Z",
        approved_at: "2026-08-29T11:00:00Z",
        dispatched_at: "2026-08-29T12:00:00Z",
        received_at: null,
        ownership_mode_snapshot: "OWNED",
        created_at: "2026-08-29T10:00:00Z",
        updated_at: "2026-08-29T12:00:00Z",
        lines: [],
      },
    ],
    isLoading: false,
    error: null,
  })),
  useTransfer: vi.fn(() => ({
    data: {
      id: "t-001",
      transfer_number: "TF-001",
      source_warehouse_id: "wh-1",
      destination_warehouse_id: "wh-2",
      state: "DRAFT",
      requested_by: "u-1",
      approved_by: null,
      requested_at: "2026-08-30T10:00:00Z",
      approved_at: null,
      dispatched_at: null,
      received_at: null,
      ownership_mode_snapshot: "OWNED",
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:00:00Z",
      lines: [
        {
          id: "line-1",
          stock_transfer_id: "t-001",
          product_id: "p-1",
          lot_id: null,
          qty_requested: "50",
          qty_dispatched: "0",
          qty_received: "0",
          unit_cost: "10.00",
          qty_variance: "0",
        },
      ],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  })),
  useTransferHistory: vi.fn(() => ({
    data: [
      {
        id: "h-1",
        stock_transfer_id: "t-001",
        actor_user_id: "u-1",
        from_state: "DRAFT",
        to_state: "DRAFT",
        event_at: "2026-08-30T10:00:00Z",
        note: null,
      },
    ],
  })),
  useCreateTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useSubmitTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useApproveTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useDispatchTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useReceiveTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useCancelTransfer: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
}));

vi.mock("@/api/hooks/useWarehouses", () => ({
  useWarehouses: vi.fn(() => ({
    data: [
      { id: "wh-1", code: "MAIN", name: "Main Warehouse" },
      { id: "wh-2", code: "BRANCH", name: "Branch Warehouse" },
      { id: "wh-3", code: "REMOTE", name: "Remote Warehouse" },
    ],
  })),
}));

vi.mock("@/api/hooks/useProducts", () => ({
  useProducts: vi.fn(() => ({
    data: [{ id: "p-1", sku: "WIDGET-001", name: "Widget" }],
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

import { TransferListPage } from "./TransferListPage";
import { TransferDetailPage } from "./TransferDetailPage";
import { TransferCreatePage } from "./TransferCreatePage";
import { ALLOWED_TRANSITIONS } from "./TransferTransitionActions";

describe("TransferListPage", () => {
  it("renders transfer table with data", async () => {
    render(<TransferListPage />, { wrapper });
    expect(screen.getByText("Stock Transfers")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("TF-001")).toBeInTheDocument();
      expect(screen.getByText("TF-002")).toBeInTheDocument();
    });
  });

  it("shows state filter dropdown", () => {
    render(<TransferListPage />, { wrapper });
    expect(screen.getByDisplayValue("All states")).toBeInTheDocument();
  });

  it("shows New Transfer button for users with TRANSFER_MANAGE", () => {
    render(<TransferListPage />, { wrapper });
    expect(screen.getByText("New Transfer")).toBeInTheDocument();
  });
});

describe("TransferDetailPage", () => {
  it("renders transfer header and lines", async () => {
    render(<TransferDetailPage />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("TF-001")).toBeInTheDocument();
      expect(screen.getByText("Transfer Lines")).toBeInTheDocument();
      expect(screen.getByText("State History")).toBeInTheDocument();
    });
  });

  it("renders line data", async () => {
    render(<TransferDetailPage />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("50")).toBeInTheDocument();
    });
  });
});

describe("TransferCreatePage", () => {
  it("renders form with warehouse pickers and line items", () => {
    render(<TransferCreatePage />, { wrapper });
    expect(screen.getByText("Create Stock Transfer")).toBeInTheDocument();
    expect(screen.getByText("Source Warehouse *")).toBeInTheDocument();
    expect(screen.getByText("Destination Warehouse *")).toBeInTheDocument();
    expect(screen.getByText("Transfer Lines")).toBeInTheDocument();
    expect(screen.getByText("+ Add Line")).toBeInTheDocument();
  });
});

describe("ALLOWED_TRANSITIONS", () => {
  it("DRAFT can go to PENDING or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS["DRAFT"]).toContain("PENDING");
    expect(ALLOWED_TRANSITIONS["DRAFT"]).toContain("CANCELLED");
  });

  it("PENDING can go to APPROVED or CANCELLED", () => {
    expect(ALLOWED_TRANSITIONS["PENDING"]).toContain("APPROVED");
    expect(ALLOWED_TRANSITIONS["PENDING"]).toContain("CANCELLED");
  });

  it("APPROVED can go to DISPATCHED", () => {
    expect(ALLOWED_TRANSITIONS["APPROVED"]).toContain("DISPATCHED");
  });

  it("DISPATCHED can go to RECEIVED", () => {
    expect(ALLOWED_TRANSITIONS["DISPATCHED"]).toContain("RECEIVED");
  });

  it("RECEIVED is terminal", () => {
    expect(ALLOWED_TRANSITIONS["RECEIVED"]).toHaveLength(0);
  });

  it("CANCELLED is terminal", () => {
    expect(ALLOWED_TRANSITIONS["CANCELLED"]).toHaveLength(0);
  });
});
