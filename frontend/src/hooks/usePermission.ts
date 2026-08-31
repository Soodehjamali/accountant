import { useAuth } from "@/features/auth/AuthContext";

/**
 * Check if the current user holds the given permission code.
 *
 * This is **UX-level gating only**. The backend remains the sole
 * authorization source of truth (per every require_permission() and
 * scope-check in the codebase). This hook hides UI elements to
 * avoid user confusion, not to enforce security.
 */
export function usePermission(code: string): boolean {
  const { permissions } = useAuth();
  return permissions.has(code);
}
