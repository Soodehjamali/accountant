import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import App from "./App";
import type { components } from "@/api/types";

// Verify the generated type includes the portal field (type-level check).
type CurrentUserResponse = components["schemas"]["CurrentUserResponse"];
type Portal = CurrentUserResponse["portal"];
void ("office" as Portal); // compiles = field exists

vi.mock("@/api/client", () => ({
  apiClient: { GET: vi.fn(), POST: vi.fn() },
  authHeader: vi.fn(() => ({})),
  getToken: vi.fn(() => null),
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));

describe("App", () => {
  it("renders the login page when not authenticated", () => {
    localStorage.removeItem("auth_token");
    render(<App />);

    expect(
      screen.getByRole("heading", { name: /enterprise erp/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/username or email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /sign in/i }),
    ).toBeInTheDocument();
  });

  it("CurrentUserResponse type includes portal field", () => {
    // Compile-time check: if portal is missing from the generated type,
    // this test file will fail to compile.
    const example: CurrentUserResponse = {
      id: "00000000-0000-0000-0000-000000000000",
      username: "test",
      email: "test@example.com",
      status: "ACTIVE",
      portal: "office",
    };
    expect(example.portal).toBe("office");

    const repExample: CurrentUserResponse = {
      ...example,
      portal: "representative",
    };
    expect(repExample.portal).toBe("representative");
  });
});
