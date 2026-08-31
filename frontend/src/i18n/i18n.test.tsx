import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useTranslation, I18nextProvider } from "react-i18next";
import i18n from "./index";

// Mock the API client
vi.mock("@/api/client", () => ({
  apiClient: { GET: vi.fn(), POST: vi.fn() },
  authHeader: vi.fn(() => ({})),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));

vi.mock("@/api/hooks/useCommissions", () => ({
  useCommissionBalance: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/api/hooks/useOrders", () => ({
  useOrders: vi.fn(() => ({ data: [], isLoading: false })),
}));

/** Test component using useTranslation hook for proper reactivity. */
function TestShell() {
  const { t, i18n: inst } = useTranslation(["common", "login", "dashboard"]);

  const toggleLanguage = () => {
    inst.changeLanguage(inst.language === "fa" ? "en" : "fa");
  };

  return (
    <div>
      <nav data-testid="nav">
        <span data-testid="nav-dashboard">{t("nav.dashboard", { ns: "common" })}</span>
        <span data-testid="nav-orders">{t("nav.orders", { ns: "common" })}</span>
      </nav>
      <button data-testid="lang-toggle" onClick={toggleLanguage}>
        {inst.language === "fa" ? "English" : "فارسی"}
      </button>
      <span data-testid="login-title">{t("title", { ns: "login" })}</span>
      <span data-testid="dashboard-title">{t("title", { ns: "dashboard" })}</span>
      <span data-testid="sign-out">{t("shell.signOut", { ns: "common" })}</span>
    </div>
  );
}

describe("i18n", () => {
  beforeEach(() => {
    i18n.changeLanguage("en");
    document.documentElement.dir = "ltr";
    document.documentElement.lang = "en";
  });

  it("defaults to English and sets dir=ltr", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );

    await waitFor(() => {
      expect(document.documentElement.dir).toBe("ltr");
    });
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByTestId("nav-dashboard")).toHaveTextContent("Dashboard");
    expect(screen.getByTestId("login-title")).toHaveTextContent("Enterprise ERP");
    expect(screen.getByTestId("sign-out")).toHaveTextContent("Sign out");
  });

  it("language switcher changes dir to rtl and renders Persian text", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByTestId("lang-toggle"));

    await waitFor(() => {
      expect(document.documentElement.dir).toBe("rtl");
    });
    expect(document.documentElement.lang).toBe("fa");
    expect(screen.getByTestId("nav-dashboard")).toHaveTextContent("داشبورد");
    expect(screen.getByTestId("nav-orders")).toHaveTextContent("سفارشات");
    expect(screen.getByTestId("login-title")).toHaveTextContent("سیستم ERP سازمانی");
    expect(screen.getByTestId("dashboard-title")).toHaveTextContent("داشبورد نماینده");
    expect(screen.getByTestId("sign-out")).toHaveTextContent("خروج");
  });

  it("language switcher toggles back to English", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );

    // EN → FA
    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(i18n.language).toBe("fa");
    });
    expect(document.documentElement.dir).toBe("rtl");

    // FA → EN
    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(i18n.language).toBe("en");
    });
    expect(document.documentElement.dir).toBe("ltr");
    expect(screen.getByTestId("nav-dashboard")).toHaveTextContent("Dashboard");
    expect(screen.getByTestId("sign-out")).toHaveTextContent("Sign out");
  });

  it("persists language choice to localStorage", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );

    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(localStorage.getItem("app_language")).toBe("fa");
    });

    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(localStorage.getItem("app_language")).toBe("en");
    });
  });

  it("Login page title translates between languages", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );
    expect(screen.getByTestId("login-title")).toHaveTextContent("Enterprise ERP");

    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("login-title")).toHaveTextContent("سیستم ERP سازمانی");
    });
  });

  it("Dashboard page title translates between languages", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );
    expect(screen.getByTestId("dashboard-title")).toHaveTextContent("Representative Dashboard");

    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-title")).toHaveTextContent("داشبورد نماینده");
    });
  });

  it("Nav items translate between languages", async () => {
    render(
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <TestShell />
        </MemoryRouter>
      </I18nextProvider>,
    );
    expect(screen.getByTestId("nav-orders")).toHaveTextContent("Orders");

    fireEvent.click(screen.getByTestId("lang-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("nav-orders")).toHaveTextContent("سفارشات");
    });
  });
});
