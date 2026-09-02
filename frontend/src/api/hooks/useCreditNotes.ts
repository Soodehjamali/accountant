import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// List / Read
// ---------------------------------------------------------------------------

interface ListCreditNotesParams {
  skip?: number;
  limit?: number;
  invoice_id?: string;
  customer_id?: string;
}

/** Fetch credit notes with optional invoice/customer filter. */
export function useCreditNotes(params: ListCreditNotesParams = {}) {
  const { skip = 0, limit = 50, invoice_id, customer_id } = params;
  return useQuery({
    queryKey: ["credit-notes", { skip, limit, invoice_id, customer_id }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/credit-notes", {
        params: {
          query: {
            skip,
            limit,
            ...(invoice_id ? { invoice_id } : {}),
            ...(customer_id ? { customer_id } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Fetch a single credit note by ID (includes lines). */
export function useCreditNote(id: string) {
  return useQuery({
    queryKey: ["credit-notes", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/credit-notes/{credit_note_id}",
        {
          params: { path: { credit_note_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!id,
  });
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/** Create a draft credit note. */
export function useCreateCreditNote() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      invoice_id: string;
      reason_code_id: string;
      lines: Array<{
        invoice_line_id?: string | null;
        description: string;
        qty: number | string;
        unit_price: number | string;
      }>;
      reference_type?: string | null;
      reference_id?: string | null;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/credit-notes", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credit-notes"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------

/** Issue a DRAFT credit note (DRAFT -> ISSUED). */
export function useIssueCreditNote(creditNoteId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string | null) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/credit-notes/{credit_note_id}/issue",
        {
          params: { path: { credit_note_id: creditNoteId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credit-notes"] });
      queryClient.invalidateQueries({
        queryKey: ["credit-notes", creditNoteId],
      });
    },
  });
}

/** Apply an ISSUED credit note (ISSUED -> APPLIED, closes invoice). */
export function useApplyCreditNote(creditNoteId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string | null) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/credit-notes/{credit_note_id}/apply",
        {
          params: { path: { credit_note_id: creditNoteId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credit-notes"] });
      queryClient.invalidateQueries({
        queryKey: ["credit-notes", creditNoteId],
      });
      // Also invalidate invoices since apply closes the invoice
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

/** Void a DRAFT credit note (DRAFT -> VOID). */
export function useVoidCreditNote(creditNoteId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string | null) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/credit-notes/{credit_note_id}/void",
        {
          params: { path: { credit_note_id: creditNoteId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["credit-notes"] });
      queryClient.invalidateQueries({
        queryKey: ["credit-notes", creditNoteId],
      });
    },
  });
}
