import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

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
