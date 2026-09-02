import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

// ---------------------------------------------------------------------------
// Read — Configs
// ---------------------------------------------------------------------------

/** Fetch all commission configurations. */
export function useCommissionConfigs() {
  return useQuery({
    queryKey: ["commission-configs"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/commission-configs",
        { headers: authHeader() },
      );
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}

// ---------------------------------------------------------------------------
// Read — Transactions
// ---------------------------------------------------------------------------

/** Fetch commission transactions, optionally filtered by representative_id. */
export function useCommissionTransactions(params: {
  representative_id?: string;
  state_event?: string;
  skip?: number;
  limit?: number;
} = {}) {
  const { representative_id, state_event, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["commission-transactions", { representative_id, state_event, skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/commission-transactions", {
        params: {
          query: {
            skip,
            limit,
            ...(representative_id ? { representative_id: representative_id as any } : {}),
            ...(state_event ? { state_event } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}

// ---------------------------------------------------------------------------
// Read — Balance
// ---------------------------------------------------------------------------

/** Fetch the commission balance for a representative. */
export function useCommissionBalance(representativeId: string) {
  return useQuery({
    queryKey: ["commission-balance", representativeId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/representatives/{representative_id}/commission-balance",
        {
          params: { path: { representative_id: representativeId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data as { representative_id: string; balance: string };
    },
    enabled: !!representativeId,
  });
}

// ---------------------------------------------------------------------------
// Write — Create Config
// ---------------------------------------------------------------------------

export function useCreateCommissionConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      rate: number;
      effective_from: string;
      effective_to?: string | null;
      representative_id?: string | null;
      product_category_id?: string | null;
      order_type: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/commission-configs", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["commission-configs"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Write — Approve
// ---------------------------------------------------------------------------

export function useApproveCommission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      transactionId,
      note,
    }: {
      transactionId: string;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/commission-transactions/{transaction_id}/approve",
        {
          params: { path: { transaction_id: transactionId } },
          body: { note: note ?? null } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["commission-transactions"] });
      queryClient.invalidateQueries({ queryKey: ["commission-balance"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Write — Pay
// ---------------------------------------------------------------------------

export function usePayCommission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      transactionId,
      note,
    }: {
      transactionId: string;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/commission-transactions/{transaction_id}/pay",
        {
          params: { path: { transaction_id: transactionId } },
          body: { note: note ?? null } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["commission-transactions"] });
      queryClient.invalidateQueries({ queryKey: ["commission-balance"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Write — Clawback
// ---------------------------------------------------------------------------

export function useClawbackCommission() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      transactionId,
      note,
    }: {
      transactionId: string;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/commission-transactions/{transaction_id}/clawback",
        {
          params: { path: { transaction_id: transactionId } },
          body: { note: note ?? null } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["commission-transactions"] });
      queryClient.invalidateQueries({ queryKey: ["commission-balance"] });
    },
  });
}
