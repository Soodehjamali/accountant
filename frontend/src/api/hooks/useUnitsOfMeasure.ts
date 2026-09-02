import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Delete a unit of measure by ID. */
export function useDeleteUnitOfMeasure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (uomId: string) => {
      const { error } = await apiClient.DELETE("/api/v1/units-of-measure/{uom_id}", {
        params: { path: { uom_id: uomId } },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["units-of-measure"] });
    },
  });
}

/** Fetch all units of measure, optionally filtered by class (BASE / DERIVED). */
export function useUnitsOfMeasure(class_?: string) {
  return useQuery({
    queryKey: ["units-of-measure", { class_ }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/units-of-measure", {
        params: {
          query: {
            ...(class_ ? { class_: class_ as any } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    staleTime: 5 * 60_000, // Reference data rarely changes — cache 5 min
  });
}
