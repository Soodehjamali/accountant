import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Fetch all active warehouses. */
export function useWarehouses() {
  return useQuery({
    queryKey: ["warehouses"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/warehouses", {
        params: { query: { limit: 100 } },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Create a new warehouse. */
export function useCreateWarehouse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      code: string;
      name: string;
      type: "FACTORY" | "REPRESENTATIVE";
      ownership_mode: "OWNED" | "CONSIGNMENT";
      address?: string;
      city_ref_id?: string;
      latitude?: number;
      longitude?: number;
      responsible_user_id?: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/warehouses", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["warehouses"] });
    },
  });
}
