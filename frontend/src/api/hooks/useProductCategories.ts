import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

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
