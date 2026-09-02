/**
 * Extract a human-readable error message from any error type.
 *
 * Handles:
 * - Standard Error objects (returns err.message)
 * - openapi-fetch error objects ({ status, data: { detail: ... } })
 * - FastAPI validation error arrays ({ detail: [{loc, msg, type}, ...] })
 * - FastAPI simple error strings ({ detail: "string" })
 * - Plain strings
 * - Unknown/null/undefined values
 *
 * Returns a single readable string, never [object Object].
 */
export function extractErrorMessage(error: unknown): string {
  // Null/undefined
  if (error == null) {
    return "An unknown error occurred.";
  }

  // Standard Error object
  if (error instanceof Error) {
    return error.message || "An unknown error occurred.";
  }

  // String
  if (typeof error === "string") {
    return error;
  }

  // Object with shape like openapi-fetch errors: { status, statusText, data }
  if (typeof error === "object") {
    const obj = error as Record<string, unknown>;

    // openapi-fetch: { data: { detail: ... } }
    if (obj.data && typeof obj.data === "object") {
      const data = obj.data as Record<string, unknown>;
      return extractDetailField(data.detail);
    }

    // Direct: { detail: ... }
    if ("detail" in obj) {
      return extractDetailField(obj.detail);
    }

    // Direct: { message: "..." }
    if (typeof obj.message === "string") {
      return obj.message;
    }

    // Direct: { error: "..." }
    if (typeof obj.error === "string") {
      return obj.error;
    }

    // Last resort: try JSON.stringify
    try {
      const str = JSON.stringify(obj);
      if (str && str !== "{}") {
        return str;
      }
    } catch {
      // stringify failed
    }
  }

  // Last resort: String()
  return String(error) || "An unknown error occurred.";
}

/**
 * Extract a readable message from a FastAPI `detail` field.
 *
 * FastAPI returns errors as:
 * - Simple: `{ detail: "Invalid SKU" }` → "Invalid SKU"
 * - Validation: `{ detail: [{loc: ["body", "sku"], msg: "field required"}, ...] }`
 *   → "sku: field required; name: field required"
 */
function extractDetailField(detail: unknown): string {
  if (detail == null) {
    return "An unknown error occurred.";
  }

  // Simple string
  if (typeof detail === "string") {
    return detail;
  }

  // Array of validation errors (FastAPI 422)
  if (Array.isArray(detail)) {
    const messages: string[] = [];
    for (const item of detail) {
      if (item && typeof item === "object") {
        const err = item as Record<string, unknown>;
        const msg = typeof err.msg === "string" ? err.msg : "Invalid value";
        const loc = Array.isArray(err.loc) ? err.loc : [];
        // Build a readable field path like "body.sku"
        const fieldPath = loc
          .filter((l: unknown) => typeof l === "string")
          .join(".");
        if (fieldPath) {
          messages.push(`${fieldPath}: ${msg}`);
        } else {
          messages.push(msg);
        }
      } else if (typeof item === "string") {
        messages.push(item);
      }
    }
    return messages.length > 0 ? messages.join("\n") : "Validation error.";
  }

  // Object (some other shape)
  if (typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return "An error occurred.";
    }
  }

  return String(detail);
}

export default extractErrorMessage;
