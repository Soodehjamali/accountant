import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiClient, authHeader, getToken, setToken, clearToken } from "@/api/client";
import type { components } from "@/api/types";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Current user profile from GET /auth/me. */
type CurrentUser = components["schemas"]["CurrentUserResponse"];


interface AuthState {
  token: string | null;
  user: CurrentUser | null;
  permissions: Set<string>;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<CurrentUser>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(getToken);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(true);

  // On mount, if a token exists, fetch user + permissions.
  useEffect(() => {
    if (!token) {
      setIsLoading(false);
      return;
    }

    async function loadUser() {
      try {
        const [meResult, permsResult] = await Promise.all([
          apiClient.GET("/api/v1/auth/me", { headers: authHeader() }),
          apiClient.GET("/api/v1/rbac/me/permissions", { headers: authHeader() }),
        ]);

        if (meResult.error || permsResult.error) {
          throw new Error("Failed to load user");
        }

        setUser(meResult.data);
        setPermissions(new Set(permsResult.data.permission_codes));
      } catch {
        // Token invalid or expired — clear it.
        clearToken();
        setTokenState(null);
      } finally {
        setIsLoading(false);
      }
    }

    loadUser();
  }, [token]);

  const login = useCallback(async (username: string, password: string) => {
    const { data, error } = await apiClient.POST("/api/v1/auth/login", {
      body: { username_or_email: username, password },
    });
    if (error) throw new Error(extractErrorMessage(error));
    setToken(data.access_token);
    setTokenState(data.access_token);

    // Fetch the user profile so the caller can use `portal` for routing
    // immediately, without waiting for the mount-effect to fire.
    const meResult = await apiClient.GET("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (meResult.error) throw new Error("Failed to load user profile");
    setUser(meResult.data);
    return meResult.data;
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState(null);
    setUser(null);
    setPermissions(new Set());
  }, []);

  const value = useMemo(
    () => ({ token, user, permissions, isLoading, login, logout }),
    [token, user, permissions, isLoading, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Access the current auth state. Throws if used outside AuthProvider. */
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
