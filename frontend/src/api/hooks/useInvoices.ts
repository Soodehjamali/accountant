import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// List / Read
// ---------------------------------------------------------------------------

interface ListInvoicesParams {
  skip?: number;
  limit?: number;
  state?: string;
  customer_id?: string;
  order_id?: string;
}

/** Fetch invoices with pagination and optional state/order filter. */
export function useInvoices(params: ListInvoicesParams = {}) {
  const { skip = 0, limit = 50, state, customer_id, order_id } = params;
  return useQuery({
    queryKey: ["invoices", { skip, limit, state, customer_id, order_id }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/invoices", {
        params: {
          query: {
            skip,
            limit,
            ...(state ? { state: state as any } : {}),
            ...(customer_id ? { customer_id } : {}),
            ...(order_id ? { order_id } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Fetch a single invoice by ID (includes lines). */
export function useInvoice(id: string) {
  return useQuery({
    queryKey: ["invoices", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/invoices/{invoice_id}",
        {
          params: { path: { invoice_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!id,
  });
}

/** Fetch an invoice's status-transition history. */
export function useInvoiceHistory(invoiceId: string) {
  return useQuery({
    queryKey: ["invoices", invoiceId, "history"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/invoices/{invoice_id}/history",
        {
          params: { path: { invoice_id: invoiceId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    enabled: !!invoiceId,
  });
}

/** Fetch payments allocated to an invoice. */
export function useInvoicePayments(invoiceId: string) {
  return useQuery({
    queryKey: ["invoices", invoiceId, "payments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/invoices/{invoice_id}/payments",
        {
          params: { path: { invoice_id: invoiceId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    enabled: !!invoiceId,
  });
}

// ---------------------------------------------------------------------------
// Transitions
// ---------------------------------------------------------------------------

/** Issue a DRAFT invoice (DRAFT -> ISSUED). */
export function useIssueInvoice(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string | null) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/invoices/{invoice_id}/issue",
        {
          params: { path: { invoice_id: invoiceId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["invoices", invoiceId] });
      queryClient.invalidateQueries({
        queryKey: ["invoices", invoiceId, "history"],
      });
    },
  });
}

/** Void a DRAFT invoice (DRAFT -> VOID). */
export function useVoidInvoice(invoiceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string | null) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/invoices/{invoice_id}/void",
        {
          params: { path: { invoice_id: invoiceId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["invoices", invoiceId] });
      queryClient.invalidateQueries({
        queryKey: ["invoices", invoiceId, "history"],
      });
    },
  });
}
