import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { describe, it, expect, vi, beforeEach } from "vitest";
import i18n from "@/i18n";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const MOCK_PRICE_LISTS = [
  {
    id: "pl-1",
    name: "لیست قیمت خرده‌فروشی",
    price_type: "RETAIL",
    currency_id: "cur-1",
    owner_scope: "GLOBAL",
    is_active: true,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  },
  {
    id: "pl-2",
    name: "لیست قیمت نمایندگی",
    price_type: "REP",
    currency_id: "cur-1",
    owner_scope: "تهران",
    is_active: false,
    created_at: "2026-08-02T00:00:00Z",
    updated_at: "2026-08-02T00:00:00Z",
  },
];

const MOCK_ENTRIES = [
  {
    id: "entry-1",
    product_id: "prod-1",
    price_list_id: "pl-1",
    currency_id: "cur-1",
    price_type: "RETAIL",
    unit_price: 50000,
    effective_from: "2026-08-01T00:00:00Z",
    effective_to: null,
    is_promo: false,
    promo_valid_from: null,
    promo_valid_to: null,
    reason: "شروع سال",
    created_at: "2026-08-01T00:00:00Z",
  },
];

const MOCK_PRODUCTS = [
  { id: "prod-1", sku: "SKU-0001", name: "محصول تست", base_uom_id: "uom-1" },
];

const MOCK_DEFAULT_CURRENCY = {
  id: "cur-1",
  code: "IRR",
  symbol: "ریال",
  decimals: 0,
  is_base: true,
};

const state = vi.hoisted(() => {
  const priceLists = [
    {
      id: "pl-1",
      name: "لیست قیمت خرده‌فروشی",
      price_type: "RETAIL",
      currency_id: "cur-1",
      owner_scope: "GLOBAL",
      is_active: true,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    },
    {
      id: "pl-2",
      name: "لیست قیمت نمایندگی",
      price_type: "REP",
      currency_id: "cur-1",
      owner_scope: "تهران",
      is_active: false,
      created_at: "2026-08-02T00:00:00Z",
      updated_at: "2026-08-02T00:00:00Z",
    },
  ];
  return {
    hasPermission: true,
    priceLists,
  };
});

vi.mock("@/features/auth/AuthContext", () => ({
  useAuth: () => ({
    token: "test-token",
    user: { id: "u1", username: "admin", email: "admin@test.com", status: "ACTIVE", portal: "office" },
    permissions: new Set(state.hasPermission ? ["PRICE_LIST_MANAGE"] : []),
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/api/hooks/usePriceLists", () => ({
  usePriceLists: () => ({ data: state.priceLists, isLoading: false, error: null }),
  usePriceList: (_id: string) => ({
    data: state.priceLists[0],
    isLoading: false,
    error: null,
  }),
  useCreatePriceList: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdatePriceList: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useSetPriceListActive: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePriceEntries: (_id: string) => ({ data: MOCK_ENTRIES, isLoading: false, error: null }),
  useAddPriceEntry: (_id: string) => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdatePriceEntry: (_id: string) => ({ mutateAsync: vi.fn(), isPending: false }),
  useCustomerPriceLists: (_id: string) => ({
    data: [
      {
        id: "assign-1",
        customer_id: "cust-1",
        price_list_id: "pl-1",
        effective_from: "2026-08-01T00:00:00Z",
        effective_to: null,
        priority: 1,
      },
    ],
    isLoading: false,
    error: null,
  }),
  useAssignCustomerPriceList: (_id: string) => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/api/hooks/useCurrency", () => ({
  useDefaultCurrency: () => ({
    data: MOCK_DEFAULT_CURRENCY,
    isLoading: false,
    error: null,
  }),
}));

vi.mock("@/api/hooks/useProducts", () => ({
  useProducts: () => ({ data: MOCK_PRODUCTS, isLoading: false, error: null }),
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

beforeEach(() => {
  state.hasPermission = true;
  state.priceLists = MOCK_PRICE_LISTS;
  i18n.changeLanguage("en");
});

// ---------------------------------------------------------------------------
// Price list list page
// ---------------------------------------------------------------------------

describe("PriceListListPage", () => {
  it("renders price lists with name, type, currency and status", async () => {
    const { PriceListListPage } = await import("./PriceListListPage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListListPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Price Lists")).toBeInTheDocument();
    expect(screen.getByText("لیست قیمت خرده‌فروشی")).toBeInTheDocument();
    expect(screen.getByText("Retail")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });

  it("shows the empty state when no price lists exist", async () => {
    state.priceLists = [];

    const { PriceListListPage } = await import("./PriceListListPage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListListPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("No price lists have been created yet."),
    ).toBeInTheDocument();
  });

  it("hides management actions without PRICE_LIST_MANAGE", async () => {
    state.hasPermission = false;
    const { PriceListListPage } = await import("./PriceListListPage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListListPage />
      </MemoryRouter>,
    );

    await screen.findByText("Price Lists");
    expect(screen.queryByText("New Price List")).not.toBeInTheDocument();
    expect(screen.queryByText("Deactivate")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Create page
// ---------------------------------------------------------------------------

describe("PriceListCreatePage", () => {
  it("renders the create form with backend schema fields", async () => {
    const { PriceListCreatePage } = await import("./PriceListCreatePage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListCreatePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Create Price List"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/list name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/price type/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/scope/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^create$/i })).toBeInTheDocument();
  });

  it("blocks the form without permission", async () => {
    state.hasPermission = false;
    const { PriceListCreatePage } = await import("./PriceListCreatePage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListCreatePage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("You do not have permission to manage price lists."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Create Price List"),
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Detail page: add price + update (new version)
// ---------------------------------------------------------------------------

describe("PriceListDetailPage", () => {
  it("renders entries and lets the admin open the update-price (new version) form", async () => {
    const { PriceListDetailPage } = await import("./PriceListDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/price-lists/pl-1"]}>
        <PriceListDetailPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("لیست قیمت خرده‌فروشی")).toBeInTheDocument();
    expect(screen.getByText("Add New Price")).toBeInTheDocument();
    // Product appears both in the select options and in the entries table.
    expect(screen.getAllByText("SKU-0001 — محصول تست").length).toBeGreaterThan(0);
    expect(screen.getByText("50,000")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByText("New version"));

    // The update form explains versioning (immutable history).
    expect(screen.getByText("Update Price")).toBeInTheDocument();
    expect(
      screen.getByText(/A new price version will be created/i),
    ).toBeInTheDocument();
  });

  it("requires a product to be selected before adding a price", async () => {
    const { PriceListDetailPage } = await import("./PriceListDetailPage");
    renderWithProviders(
      <MemoryRouter initialEntries={["/office/price-lists/pl-1"]}>
        <PriceListDetailPage />
      </MemoryRouter>,
    );

    await screen.findByText("Add New Price");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add price" }));

    expect(
      await screen.findByText("Select a product before recording a price."),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Customer price-list assignment
// ---------------------------------------------------------------------------

describe("CustomerPriceListSection", () => {
  it("shows the assigned price list and the assign form", async () => {
    const { CustomerPriceListSection } = await import("./CustomerPriceListSection");
    renderWithProviders(
      <MemoryRouter>
        <CustomerPriceListSection customerId="cust-1" />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Customer Price List"),
    ).toBeInTheDocument();
    // Assigned list name appears in the assignments table AND the select.
    expect(screen.getAllByText("لیست قیمت خرده‌فروشی").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Assign" })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Persian labels
// ---------------------------------------------------------------------------

describe("Persian UI", () => {
  it("shows Persian headings and empty states", async () => {
    await i18n.changeLanguage("fa");
    state.priceLists = [];

    const { PriceListListPage } = await import("./PriceListListPage");
    renderWithProviders(
      <MemoryRouter>
        <PriceListListPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("لیست قیمت‌ها")).toBeInTheDocument();
    expect(
      screen.getByText("هنوز لیست قیمتی ایجاد نشده است."),
    ).toBeInTheDocument();
    // "New Price List" appears in the header button and the empty-state link.
    expect(screen.getAllByText("لیست قیمت جدید").length).toBeGreaterThan(0);
  });
});