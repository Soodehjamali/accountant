import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

/** Fetch reason codes, optionally filtered by scope. */
export function useReasonCodes(scope?: string) {
  return useQuery({
    queryKey: ["reason-codes", { scope }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/reason-codes", {
        params: {
          query: {
            ...(scope ? { scope: scope as any } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}
