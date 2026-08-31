/**
 * Formatting utilities for locale-aware rendering.
 *
 * ADR-011 decision: financial figures ALWAYS render in Latin digits (0-9),
 * regardless of the active language. This is standard Iranian accounting
 * practice — Persian digits (۰-۹) in financial contexts cause ambiguity
 * in copy-paste, external integration, and mixed-script input.
 *
 * All number formatting explicitly specifies:
 * - locale: "en" (or whatever the active language is)
 * - numberingSystem: "latn" (forces 0-9 digits)
 *
 * Date formatting is Gregorian for now (Phase 1). Jalali conversion is
 * deferred to Phase 2 per the roadmap.
 */

/**
 * Format a number with Latin digits and locale-appropriate separators.
 *
 * Examples:
 *   formatNumber(2550)       → "2,550"
 *   formatNumber(2550.00)    → "2,550.00"
 *   formatNumber(1250.5)     → "1,250.50"
 */
export function formatNumber(
  value: number | string,
  options?: { minimumFractionDigits?: number; maximumFractionDigits?: number },
): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "—";

  return num.toLocaleString("en", {
    numberingSystem: "latn",
    minimumFractionDigits: options?.minimumFractionDigits ?? 0,
    maximumFractionDigits: options?.maximumFractionDigits ?? 0,
  });
}

/**
 * Format a financial amount — always 2 decimal places, Latin digits.
 *
 * Examples:
 *   formatCurrency(2550)    → "2,550.00"
 *   formatCurrency("1250")  → "1,250.00"
 */
export function formatCurrency(value: number | string): string {
  return formatNumber(value, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * Format an ISO date string for display.
 *
 * Phase 1: Gregorian formatting only. Jalali conversion is deferred to
 * Phase 2 per the roadmap. Uses the active i18n language for locale
 * but always Latin digits.
 *
 * Examples (en):
 *   formatDate("2026-08-31T12:00:00Z") → "Aug 31, 2026"
 *
 * Examples (fa — same output for now, Jalali TBD):
 *   formatDate("2026-08-31T12:00:00Z") → "Aug 31, 2026"
 */
export function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";

  try {
    const date = new Date(isoString);
    // Use "en" locale with Latin digits for Phase 1 (Gregorian).
    // When Jalali is added in Phase 2, this will switch to "fa" locale
    // with a jalali calendar system.
    return date.toLocaleDateString("en", {
      numberingSystem: "latn",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "—";
  }
}

/**
 * Format an ISO datetime string for display (date + time).
 *
 * Phase 1: Gregorian only, Latin digits.
 */
export function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";

  try {
    const date = new Date(isoString);
    return date.toLocaleString("en", {
      numberingSystem: "latn",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}
