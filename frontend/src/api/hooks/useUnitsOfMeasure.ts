import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

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
