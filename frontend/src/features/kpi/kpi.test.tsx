import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/usePermission", () => ({
  usePermission: vi.fn(() => true),
}));

const mockLatestValues: Record<string, { value: string; captured_at: string } | null> = {
  TOTAL_STOCK_VALUE: { value: "2550.00", captured_at: "2026-08-30T10:00:00Z" },
  AR_BALANCE: { value: "300.00", captured_at: "2026-08-30T10:00:00Z" },
  COMMISSION_PAYABLE: null,
};

const mockHistory: Record<string, Array<{
  id: string;
  kpi_key: string;
  scope_type: string;
  scope_id: string | null;
  value: string;
  captured_at: string;
  period_granularity: string;
}>> = {
  TOTAL_STOCK_VALUE: [
    {
      id: "kpi-1",
      kpi_key: "TOTAL_STOCK_VALUE",
      scope_type: "GLOBAL",
      scope_id: null,
      value: "2550.00",
      captured_at: "2026-08-30T10:00:00Z",
      period_granularity: "MONTHLY",
    },
    {
      id: "kpi-2",
      kpi_key: "TOTAL_STOCK_VALUE",
      scope_type: "GLOBAL",
      scope_id: null,
      value: "2000.00",
      captured_at: "2026-07-31T10:00:00Z",
      period_granularity: "MONTHLY",
    },
  ],
  AR_BALANCE: [],
  COMMISSION_PAYABLE: [],
};

vi.mock("@/api/hooks/useKpi", () => ({
  useKpiLatest: vi.fn((kpiKey: string) => ({
    data: mockLatestValues[kpiKey] ?? null,
    isLoading: false,
  })),
  useKpiHistory: vi.fn((params: { kpiKey: string }) => ({
    data: mockHistory[params.kpiKey] ?? [],
    isLoading: false,
  })),
  useCaptureKpi: vi.fn(() => ({
    mutateAsync: vi.fn().mockResolvedValue({
      items: [],
      captured_at: "2026-08-30T12:00:00Z",
    }),
    isPending: false,
    isSuccess: false,
    isError: false,
    data: null,
  })),
}));

vi.mock("@/api/client", () => ({
  apiClient: {
    GET: vi.fn().mockResolvedValue({ data: { items: [] }, error: null }),
    POST: vi.fn().mockResolvedValue({ data: {}, error: null }),
  },
  authHeader: vi.fn(() => ({})),
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

describe("KpiDashboardPage", () => {
  it("renders the page heading and description", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("KPI Dashboard")).toBeInTheDocument();
    expect(
      screen.getByText(/Global key performance indicators/),
    ).toBeInTheDocument();
  });

  it("renders all 3 KPI cards with correct labels", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Total Stock Value")).toBeInTheDocument();
    expect(screen.getByText("Accounts Receivable")).toBeInTheDocument();
    expect(screen.getByText("Commission Payable")).toBeInTheDocument();
  });

  it("displays latest values for cards that have data", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );
    // TOTAL_STOCK_VALUE = 2550.00
    expect(screen.getByText("2,550.00")).toBeInTheDocument();
    // AR_BALANCE = 300.00
    expect(screen.getByText("300.00")).toBeInTheDocument();
  });

  it("shows 'No data' for cards without captured values", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("No data")).toBeInTheDocument();
  });

  it("shows history panel when a card is clicked", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );

    // Click the Total Stock Value card
    fireEvent.click(screen.getByText("Total Stock Value"));

    // History panel should appear
    expect(
      screen.getByText(/TOTAL STOCK VALUE — History/),
    ).toBeInTheDocument();
    expect(screen.getByText("Monthly")).toBeInTheDocument();
  });

  it("shows history table rows when history data exists", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );

    // Click the Total Stock Value card (has 2 history entries)
    fireEvent.click(screen.getByText("Total Stock Value"));

    // Both history values should render (2,550.00 appears in card + history)
    const all2550 = screen.getAllByText("2,550.00");
    expect(all2550.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("2,000.00")).toBeInTheDocument();
  });

  it("hides history panel when card is clicked again", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );

    // Open
    fireEvent.click(screen.getByText("Total Stock Value"));
    expect(
      screen.getByText(/TOTAL STOCK VALUE — History/),
    ).toBeInTheDocument();

    // Close
    fireEvent.click(screen.getByText("Total Stock Value"));
    expect(
      screen.queryByText(/TOTAL STOCK VALUE — History/),
    ).not.toBeInTheDocument();
  });

  it("shows Capture KPIs button", async () => {
    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Capture KPIs")).toBeInTheDocument();
  });

  it("captures per-card history when different keys are selected", async () => {
    const { useKpiHistory } = await import("@/api/hooks/useKpi");
    const mockFn = vi.fn().mockReturnValue({ data: [], isLoading: false });
    (useKpiHistory as ReturnType<typeof vi.fn>).mockImplementation(mockFn);

    const { KpiDashboardPage } = await import("./KpiDashboardPage");
    renderWithProviders(
      <MemoryRouter>
        <KpiDashboardPage />
      </MemoryRouter>,
    );

    // Click Total Stock Value
    fireEvent.click(screen.getByText("Total Stock Value"));
    expect(mockFn).toHaveBeenCalledWith(
      expect.objectContaining({ kpiKey: "TOTAL_STOCK_VALUE" }),
    );

    // Click AR Balance (switch)
    fireEvent.click(screen.getByText("Accounts Receivable"));
    expect(mockFn).toHaveBeenCalledWith(
      expect.objectContaining({ kpiKey: "AR_BALANCE" }),
    );
  });
});
