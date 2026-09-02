import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// Read — List
// ---------------------------------------------------------------------------

/** Fetch customer returns with optional filters. */
export function useReturnsList(params: {
  customer_id?: string;
  state?: string;
  skip?: number;
  limit?: number;
} = {}) {
  const { customer_id, state, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["customer-returns", "list", { customer_id, state, skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/customer-returns" as any, {
        params: {
          query: {
            skip,
            limit,
            ...(customer_id ? { customer_id: customer_id as any } : {}),
            ...(state ? { state } : {}),
          },
        } as any,
        headers: authHeader(),
      } as any);
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items;
    },
  });
}

// ---------------------------------------------------------------------------
// Read — Single
// ---------------------------------------------------------------------------

/** Fetch a single customer return with its lines. */
export function useReturn(id: string) {
  return useQuery({
    queryKey: ["customer-returns", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/customer-returns/{return_id}" as any,
        {
          params: { path: { return_id: id } } as any,
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!id,
  });
}

// ---------------------------------------------------------------------------
// Write — Create
// ---------------------------------------------------------------------------

export function useCreateReturn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      order_id?: string | null;
      customer_id?: string | null;
      representative_id?: string | null;
      warehouse_id: string;
      reason_code_id: string;
      return_type: string;
      note?: string | null;
      lines: Array<{
        product_id: string;
        order_line_id?: string | null;
        qty_returned: number | string;
        unit_refund_amount?: number | string;
      }>;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/customer-returns" as any, {
        body: body as any,
        headers: authHeader(),
      } as any);
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customer-returns"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Write — Transitions
// ---------------------------------------------------------------------------

function useReturnTransition(action: "receive" | "inspect" | "close") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      returnId,
      note,
    }: {
      returnId: string;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        `/api/v1/customer-returns/{return_id}/${action}` as any,
        {
          params: { path: { return_id: returnId } } as any,
          body: { note: note ?? null } as any,
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customer-returns"] });
    },
  });
}

export function useReceiveReturn() {
  return useReturnTransition("receive");
}

export function useInspectReturn() {
  return useReturnTransition("inspect");
}

export function useCloseReturn() {
  return useReturnTransition("close");
}
