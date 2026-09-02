import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

// ---------------------------------------------------------------------------
// Read — List
// ---------------------------------------------------------------------------

/** Fetch all payments, optionally filtered by customer_id. */
export function usePaymentsList(params: {
  customer_id?: string;
  skip?: number;
  limit?: number;
} = {}) {
  const { customer_id, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["payments", "list", { customer_id, skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/payments" as any, {
        params: {
          query: {
            skip,
            limit,
            ...(customer_id ? { customer_id: customer_id as any } : {}),
          },
        } as any,
        headers: authHeader(),
      } as any);
      if (error) throw new Error(String(error));
      return (data as any).items;
    },
  });
}

// ---------------------------------------------------------------------------
// Read — Single
// ---------------------------------------------------------------------------

/** Fetch a single payment by ID (includes allocations). */
export function usePayment(id: string) {
  return useQuery({
    queryKey: ["payments", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/payments/{payment_id}",
        {
          params: { path: { payment_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    enabled: !!id,
  });
}

// ---------------------------------------------------------------------------
// Create (record payment with allocations)
// ---------------------------------------------------------------------------

/** Record a payment with allocations to one or more invoices. */
export function useRecordPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      customer_id: string;
      currency_id: string;
      amount: number | string;
      method: string;
      reference?: string | null;
      received_at?: string | null;
      allocations: Array<{
        invoice_id: string;
        allocated_amount: number | string;
      }>;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/payments", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["payments"] });
      // Also invalidate invoices since their balance_due changes
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}
