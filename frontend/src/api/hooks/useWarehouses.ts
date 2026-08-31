import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

/** Fetch all active warehouses. */
export function useWarehouses() {
  return useQuery({
    queryKey: ["warehouses"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/warehouses", {
        params: { query: { limit: 100 } },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}
