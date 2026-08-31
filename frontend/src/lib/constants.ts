/** Backend API base URL (from Vite env or default to proxy path). */
/** Backend API base URL — Electron desktop app injects this at runtime; web build falls back to Vite env or proxy path. */
export const API_BASE_URL =
  (typeof window !== "undefined" && (window as any).__BACKEND_URL__) ||
  (import.meta.env.VITE_API_BASE_URL ?? "");

/** Permission codes matching backend's RBAC permission codes. */
export const PERMISSIONS = {
  CUSTOMER_MANAGE: "CUSTOMER_MANAGE",
  ORDER_MANAGE: "ORDER_MANAGE",
  ORDER_APPROVE: "ORDER_APPROVE",
  INVOICE_MANAGE: "INVOICE_MANAGE",
  TRANSFER_MANAGE: "TRANSFER_MANAGE",
  PAYMENT_MANAGE: "PAYMENT_MANAGE",
  COMMISSION_MANAGE: "COMMISSION_MANAGE",
  CREDIT_NOTE_MANAGE: "CREDIT_NOTE_MANAGE",
  INVENTORY_MANAGE: "INVENTORY_MANAGE",
  PRODUCT_MANAGE: "PRODUCT_MANAGE",
  AUDIT_LOG_VIEW: "AUDIT_LOG_VIEW",
  KPI_SNAPSHOT_VIEW: "KPI_SNAPSHOT_VIEW",
  REPORT_MANAGE: "REPORT_MANAGE",
  RBAC_MANAGE: "RBAC_MANAGE",
} as const;

/** Route paths. */
export const ROUTES = {
  LOGIN: "/login",
  OFFICE: "/office",
  REP: "/rep",
  HOME: "/",
} as const;
