import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

/** Fetch the live balance for a customer. */
export function useCustomerBalance(customerId: string) {
  return useQuery({
    queryKey: ["customer-balance", customerId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/customers/{customer_id}/balance" as any,
        {
          params: { path: { customer_id: customerId } } as any,
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(String(error));
      return data as { customer_id: string; balance: string; computed_at: string };
    },
    enabled: !!customerId,
  });
}

/** Fetch ledger entries for a customer, optionally filtered by date range and entry type. */
export function useCustomerLedgerEntries(
  customerId: string,
  params: {
    entry_type?: string;
    skip?: number;
    limit?: number;
  } = {},
) {
  const { entry_type, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["customer-ledger", customerId, { entry_type, skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/customers/{customer_id}/ledger" as any,
        {
          params: {
            path: { customer_id: customerId },
            query: {
              skip,
              limit,
              ...(entry_type ? { entry_type } : {}),
            },
          } as any,
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(String(error));
      return (data as any).items;
    },
    enabled: !!customerId,
  });
}
