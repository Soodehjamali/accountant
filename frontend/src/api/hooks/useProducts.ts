import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

/** Fetch all products (optionally excluding discontinued). */
export function useProducts(includeDiscontinued = true) {
  return useQuery({
    queryKey: ["products", { includeDiscontinued }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/products", {
        params: { query: { include_discontinued: includeDiscontinued } },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}

/** Fetch a single product by SKU. */
export function useProduct(sku: string) {
  return useQuery({
    queryKey: ["products", sku],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/products/{sku}", {
        params: { path: { sku } },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    enabled: !!sku,
  });
}

/** Create a new product. */
export function useCreateProduct() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      sku: string;
      name: string;
      description?: string;
      base_uom_id: string;
      category_id?: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/products", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["products"] });
    },
  });
}
