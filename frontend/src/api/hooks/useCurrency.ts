import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Fetch the default (base) currency — returns the IRR currency with its real UUID. */
export function useDefaultCurrency() {
  return useQuery({
    queryKey: ["default-currency"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/currencies/default" as any,
        {
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data as { id: string; code: string; symbol: string; decimals: number; is_base: boolean };
    },
    staleTime: 5 * 60_000, // Currency rarely changes — cache 5 min
  });
}
