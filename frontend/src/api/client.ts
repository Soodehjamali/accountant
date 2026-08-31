import createClient from "openapi-fetch";
import type { paths } from "./types";
import { API_BASE_URL } from "@/lib/constants";

// ---------------------------------------------------------------------------
// Electron API types
// ---------------------------------------------------------------------------

interface ElectronAPI {
  getConfig: () => Promise<{ backendUrl: string | null }>;
  setConfig: (config: { backendUrl: string }) => Promise<{ ok: boolean }>;
  reloadApp: () => Promise<void>;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

// ---------------------------------------------------------------------------
// Auth token management
// ---------------------------------------------------------------------------

const AUTH_TOKEN_KEY = "auth_token";

/** Read the stored JWT token from localStorage. */
export function getToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

/** Store the JWT token in localStorage. */
export function setToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

/** Remove the JWT token (logout). */
export function clearToken(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

/** Returns Authorization header object if a token is stored. */
export function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ---------------------------------------------------------------------------
// Lazy backend URL resolution
// ---------------------------------------------------------------------------

/**
 * Resolve the backend base URL.
 *
 * Priority:
 * 1. Electron preload (synchronous IPC, set on preload window before page
 *    scripts run). With contextIsolation, this value is NOT directly
 *    accessible from page scripts — but the preload script also registers
 *    `window.electronAPI.getConfig()` via contextBridge which IS accessible.
 * 2. Vite env variable (VITE_API_BASE_URL) — used in web deployment
 * 3. Empty string (same-origin proxy, used in development with Vite proxy)
 *
 * The resolution is lazy — called on first API request, not at module load.
 * This avoids a race with Electron's contextIsolation boundary.
 */
let _resolvedBaseUrl: string | undefined = undefined;

async function resolveBaseUrl(): Promise<string> {
  if (_resolvedBaseUrl !== undefined) return _resolvedBaseUrl;

  // In Electron: read from the preload-exposed IPC
  if (typeof window !== "undefined" && window.electronAPI) {
    try {
      const config = await window.electronAPI.getConfig();
      if (config.backendUrl) {
        _resolvedBaseUrl = config.backendUrl;
        return _resolvedBaseUrl;
      }
    } catch {
      // Config read failed — fall through to env/proxy
    }
  }

  // Web deployment: use Vite env variable or empty string (same-origin proxy)
  _resolvedBaseUrl = API_BASE_URL;
  return API_BASE_URL;
}

// ---------------------------------------------------------------------------
// Type-safe API client (lazy initialization)
// ---------------------------------------------------------------------------

/**
 * Type-safe API client generated from the backend OpenAPI schema.
 *
 * The client is lazily initialized on first use to avoid a race with
 * Electron's contextIsolation boundary (preload scripts set config values
 * before page scripts run, but contextIsolation prevents direct access
 * to preload-defined globals from page code).
 *
 * Usage:
 *   const client = await getApiClient();
 *   const { data, error } = await client.GET("/api/v1/products", {
 *     params: { query: { include_discontinued: true } },
 *     headers: authHeader(),
 *   });
 */
let _apiClient: ReturnType<typeof createClient<paths>> | null = null;

export async function getApiClient(): Promise<ReturnType<typeof createClient<paths>>> {
  if (_apiClient) return _apiClient;

  const baseUrl = await resolveBaseUrl();
  _apiClient = createClient<paths>({ baseUrl });
  return _apiClient;
}

/**
 * Synchronous accessor for the API client.
 *
 * Returns the client if already initialized (after first API call).
 * Returns null if not yet initialized — callers in non-async contexts
 * should use getApiClient() instead.
 */
export function getApiClientSync(): ReturnType<typeof createClient<paths>> | null {
  return _apiClient;
}

/**
 * @deprecated Use `await getApiClient()` instead. This export exists for
 * backward compatibility with existing code that imports `apiClient` directly.
 * It will use the default (empty string / proxy) base URL until the lazy
 * resolution completes. Prefer the async accessor.
 */
export const apiClient = createClient<paths>({
  baseUrl: API_BASE_URL,
});
