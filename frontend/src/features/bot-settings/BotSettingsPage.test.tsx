import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "@/i18n";
import i18n from "@/i18n";
import { BotSettingsPage } from "./BotSettingsPage";

vi.mock("@/api/client", () => ({
  apiClient: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() },
  authHeader: vi.fn(() => ({})),
  getToken: vi.fn(() => null),
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const apiClient = (await import("@/api/client")).apiClient as any;

const SECRET_TOKEN = "1234567890:RAW_SECRET_VALUE_NEVER_SHOWN";

function makeConfigItem(overrides: Record<string, unknown> = {}) {
  return {
    platform: "TELEGRAM",
    enabled: false,
    token_configured: false,
    token_hint: null,
    status: "NOT_CONFIGURED",
    last_heartbeat: null,
    bot_username: null,
    bot_name: null,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BotSettingsPage />
    </QueryClientProvider>,
  );
}

describe("BotSettingsPage", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    await i18n.changeLanguage("en");
  });

  it("saves the entered token through the API (save flow)", async () => {
    const user = userEvent.setup();
    apiClient.GET.mockResolvedValue({
      data: { items: [makeConfigItem()] },
      error: undefined,
    });
    apiClient.PUT.mockResolvedValue({
      data: { ok: true, platform: "TELEGRAM", enabled: false, token_configured: true, token_hint: SECRET_TOKEN.slice(-4) },
      error: undefined,
    });

    renderPage();

    const input = await screen.findByPlaceholderText(/enter token/i);
    await user.type(input, SECRET_TOKEN);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiClient.PUT).toHaveBeenCalledWith(
        "/api/v1/bot-config/{platform}",
        expect.objectContaining({
          params: { path: { platform: "telegram" } },
          body: { enabled: false, token: SECRET_TOKEN },
        }),
      );
    });

    // After save the input is cleared and a confirmation is shown instead of
    // the token value.
    await screen.findByText("Token configured.");
    expect(input).toHaveValue("");
    expect(screen.queryByText(SECRET_TOKEN)).not.toBeInTheDocument();
  });

  it("keeps the token input LTR and submits a pasted token exactly as-is", async () => {
    const user = userEvent.setup();
    // An LTR token whose reversed form differs, so any RTL reordering would
    // change the submitted value and fail this assertion.
    const ltrToken = "123456789:AAbBcCdDeEfF";
    expect(ltrToken).not.toBe(ltrToken.split("").reverse().join(""));

    apiClient.GET.mockResolvedValue({
      data: { items: [makeConfigItem()] },
      error: undefined,
    });
    apiClient.PUT.mockResolvedValue({
      data: { ok: true, platform: "TELEGRAM", enabled: false, token_configured: true, token_hint: ltrToken.slice(-4) },
      error: undefined,
    });

    renderPage();

    const input = await screen.findByPlaceholderText(/enter token/i);
    // The token is an LTR string: the field must keep LTR direction and
    // left alignment even inside the RTL (Persian) page.
    expect(input).toHaveAttribute("dir", "ltr");
    expect(input.className).toContain("text-left");

    // user.paste targets the focused element, so focus the token input first.
    await user.click(input);
    await user.paste(ltrToken);
    expect(input).toHaveValue(ltrToken);

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiClient.PUT).toHaveBeenCalledWith(
        "/api/v1/bot-config/{platform}",
        expect.objectContaining({
          params: { path: { platform: "telegram" } },
          body: { enabled: false, token: ltrToken },
        }),
      );
    });
  });

  it("never renders the raw token (only the last-4 hint), input is password-type", async () => {
    apiClient.GET.mockResolvedValue({
      data: {
        items: [
          makeConfigItem({
            enabled: true,
            token_configured: true,
            token_hint: SECRET_TOKEN.slice(-4),
            bot_username: "example_bot",
            bot_name: "Example Bot",
          }),
        ],
      },
      error: undefined,
    });

    renderPage();

    // The token input is a secret/password field.
    const input = await screen.findByPlaceholderText(/enter a new token to replace/i);
    expect(input).toHaveAttribute("type", "password");

    // Only the masked hint is shown -- never the raw value.
    expect(screen.getByText(`•••• ${SECRET_TOKEN.slice(-4)}`)).toBeInTheDocument();
    expect(screen.queryByText(SECRET_TOKEN)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET_TOKEN);

    // The persisted bot identity (from getMe) is displayed instead.
    expect(screen.getByText("Example Bot")).toBeInTheDocument();
    expect(screen.getByText("@example_bot")).toBeInTheDocument();
  });

  it("shows the connected message with the bot identity after a successful test", async () => {
    const user = userEvent.setup();
    apiClient.GET.mockResolvedValue({
      data: { items: [makeConfigItem({ enabled: true, token_configured: true, token_hint: "zzzz" })] },
      error: undefined,
    });
    apiClient.POST.mockResolvedValue({
      data: {
        ok: true,
        detail: "Connected as @example_bot",
        bot_username: "example_bot",
        bot_name: "Example Bot",
      },
      error: undefined,
    });

    renderPage();

    await user.click(
      await screen.findByRole("button", { name: "Test Connection" }),
    );
    expect(apiClient.POST).toHaveBeenCalledWith(
      "/api/v1/bot-config/{platform}/test",
      expect.objectContaining({ params: { path: { platform: "telegram" } } }),
    );
    await screen.findByText(/Connected as @example_bot/i);
  });
});