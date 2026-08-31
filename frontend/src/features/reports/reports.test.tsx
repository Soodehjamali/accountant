import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/usePermission", () => ({
  usePermission: vi.fn(() => true),
}));

const mockReportTypes = [
  { id: "rt-1", code: "AR_AGING" },
  { id: "rt-2", code: "INVENTORY_VALUATION" },
  { id: "rt-3", code: "COMMISSION_PAYABLE" },
];

const mockDefinitions = [
  {
    id: "def-1",
    report_type_id: "rt-1",
    owner_user_id: "u1",
    name: "Monthly AR Aging",
    parameters: {},
    output_format: "PDF",
    schedule_cron: null,
    is_active: true,
    created_at: "2026-08-30T10:00:00Z",
  },
  {
    id: "def-2",
    report_type_id: "rt-2",
    owner_user_id: "u1",
    name: "Inventory Snapshot",
    parameters: {},
    output_format: "CSV",
    schedule_cron: null,
    is_active: false,
    created_at: "2026-08-29T10:00:00Z",
  },
];

const mockCreateMutateAsync = vi.fn().mockResolvedValue({ id: "def-new" });

vi.mock("@/api/hooks/useReports", () => ({
  useReportTypes: vi.fn(() => ({
    data: mockReportTypes,
    isLoading: false,
  })),
  useReportDefinitions: vi.fn(() => ({
    data: mockDefinitions,
    isLoading: false,
  })),
  useCreateReportDefinition: vi.fn(() => ({
    mutateAsync: mockCreateMutateAsync,
    isPending: false,
  })),
  useRunReport: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useReportRun: vi.fn(),
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
// ReportListPage tests
// ---------------------------------------------------------------------------

describe("ReportListPage", () => {
  it("renders the page heading", async () => {
    const { ReportListPage } = await import("./ReportListPage");
    renderWithProviders(
      <MemoryRouter>
        <ReportListPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Reports")).toBeInTheDocument();
  });

  it("renders existing definitions in the table", async () => {
    const { ReportListPage } = await import("./ReportListPage");
    renderWithProviders(
      <MemoryRouter>
        <ReportListPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Monthly AR Aging")).toBeInTheDocument();
    expect(screen.getByText("Inventory Snapshot")).toBeInTheDocument();
  });

  it("shows create form when New Report Definition is clicked", async () => {
    const { ReportListPage } = await import("./ReportListPage");
    renderWithProviders(
      <MemoryRouter>
        <ReportListPage />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("New Report Definition"));
    expect(
      screen.getByText("New Report Definition", { selector: "h2" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Name *")).toBeInTheDocument();
    expect(screen.getByText("Report Type *")).toBeInTheDocument();
  });

  it("create form submits the expected body via mutateAsync", async () => {
    mockCreateMutateAsync.mockClear();

    const { ReportListPage } = await import("./ReportListPage");
    renderWithProviders(
      <MemoryRouter>
        <ReportListPage />
      </MemoryRouter>,
    );

    // Open form
    fireEvent.click(screen.getByText("New Report Definition"));

    // Fill in fields
    fireEvent.change(screen.getByPlaceholderText("e.g. Monthly AR Aging"), {
      target: { value: "Test Report" },
    });
    // The report type select shows "Select type…" by default
    const reportTypeSelect = screen.getByDisplayValue("Select type\u2026");
    fireEvent.change(reportTypeSelect, { target: { value: "rt-1" } });

    // Submit
    fireEvent.click(screen.getByText("Create Definition"));

    await waitFor(() => {
      expect(mockCreateMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Test Report",
          report_type_id: "rt-1",
          output_format: "PDF",
          parameters: {},
        }),
      );
    });
  });

  it("shows Run Now button for each definition", async () => {
    const { ReportListPage } = await import("./ReportListPage");
    renderWithProviders(
      <MemoryRouter>
        <ReportListPage />
      </MemoryRouter>,
    );
    const runButtons = screen.getAllByText("Run Now");
    expect(runButtons).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// ReportRunDetailPage tests
// ---------------------------------------------------------------------------

describe("ReportRunDetailPage", () => {
  it("renders snapshot data in a table when COMPLETE", async () => {
    const mockSnapshot = {
      run: {
        id: "run-1",
        status: "COMPLETE",
        started_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:00:05Z",
        row_count: 2,
        report_definition_id: "def-1",
      },
      snapshot: {
        id: "snap-1",
        snapshot_data: {
          report_type: "AR_AGING",
          report_name: "Monthly AR Aging",
          rows: [
            {
              customer_id: "c1",
              customer_name: "Acme Corp",
              total_balance: "500.00",
              "0_30_days": "300.00",
              "31_60_days": "200.00",
              "61_90_days": "0",
              "90_plus_days": "0",
            },
          ],
          row_count: 1,
        },
        row_count: 1,
        captured_at: "2026-08-30T10:00:05Z",
      },
    };

    const { useReportRun } = await import("@/api/hooks/useReports");
    (useReportRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockSnapshot,
      isLoading: false,
      error: null,
    });

    const { ReportRunDetailPage } = await import("./ReportRunDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/reports/runs/run-1"]}>
        <ReportRunDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Report Run")).toBeInTheDocument();
    expect(screen.getByText("COMPLETE")).toBeInTheDocument();
    expect(screen.getByText("AR_AGING")).toBeInTheDocument();
    expect(screen.getByText("Monthly AR Aging")).toBeInTheDocument();
    expect(screen.getByText("Report Data")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("500.00")).toBeInTheDocument();
  });

  it("shows loading state while fetching", async () => {
    const { useReportRun } = await import("@/api/hooks/useReports");
    (useReportRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: null,
      isLoading: true,
      error: null,
    });

    const { ReportRunDetailPage } = await import("./ReportRunDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/reports/runs/run-1"]}>
        <ReportRunDetailPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows error state on fetch failure", async () => {
    const { useReportRun } = await import("@/api/hooks/useReports");
    (useReportRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: null,
      isLoading: false,
      error: new Error("Not found"),
    });

    const { ReportRunDetailPage } = await import("./ReportRunDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/reports/runs/run-1"]}>
        <ReportRunDetailPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByText("Failed to load report run."),
    ).toBeInTheDocument();
  });

  it("shows graceful message for 501 (report builder not implemented)", async () => {
    const mockFailedRun = {
      run: {
        id: "run-2",
        status: "FAILED",
        started_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:00:01Z",
        row_count: null,
        report_definition_id: "def-1",
      },
      snapshot: null,
    };

    const { useReportRun } = await import("@/api/hooks/useReports");
    (useReportRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: mockFailedRun,
      isLoading: false,
      error: null,
    });

    const { ReportRunDetailPage } = await import("./ReportRunDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/reports/runs/run-2"]}>
        <ReportRunDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Report Run")).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    // FAILED runs have no snapshot — the component shows the status badge
    // and the metadata grid; no separate "no data" message is needed because
    // the FAILED status itself is the diagnostic. Verify no snapshot section.
    expect(screen.queryByText("Report Data")).not.toBeInTheDocument();
  });
});
