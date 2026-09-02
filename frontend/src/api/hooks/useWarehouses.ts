import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Delete a warehouse by ID. */
export function useDeleteWarehouse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (warehouseId: string) => {
      const { error } = await apiClient.DELETE("/api/v1/warehouses/{warehouse_id}", {
        params: { path: { warehouse_id: warehouseId } },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["warehouses"] });
    },
  });
}

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

/** Update a warehouse by ID. */
export function useUpdateWarehouse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      warehouseId,
      ...body
    }: {
      warehouseId: string;
      name?: string;
      address?: string;
      city_ref_id?: string;
      latitude?: number;
      longitude?: number;
      responsible_user_id?: string;
      status?: string;
    }) => {
      const { data, error } = await apiClient.PATCH("/api/v1/warehouses/{warehouse_id}", {
        params: { path: { warehouse_id: warehouseId } },
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
