import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Delete a product category by ID. */
export function useDeleteProductCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (categoryId: string) => {
      const { error } = await apiClient.DELETE(
        "/api/v1/product-categories/{category_id}",
        {
          params: { path: { category_id: categoryId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["product-categories"] });
    },
  });
}

/** Fetch all product categories ordered by hierarchy. */
export function useProductCategories() {
  return useQuery({
    queryKey: ["product-categories"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/product-categories",
        {
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    staleTime: 5 * 60_000, // Reference data rarely changes — cache 5 min
  });
}
