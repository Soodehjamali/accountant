import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

interface ListCustomersParams {
  skip?: number;
  limit?: number;
  search?: string;
  status?: string;
}

/** Fetch customers with pagination and optional search/status filter. */
export function useCustomers(params: ListCustomersParams = {}) {
  const { skip = 0, limit = 50, search, status } = params;
  return useQuery({
    queryKey: ["customers", { skip, limit, search, status }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/customers", {
        params: {
          query: {
            skip,
            limit,
            ...(search ? { search } : {}),
            ...(status ? { status: status as any } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}

/** Fetch a single customer by ID. */
export function useCustomer(id: string) {
  return useQuery({
    queryKey: ["customers", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/customers/{customer_id}",
        {
          params: { path: { customer_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    enabled: !!id,
  });
}

/** Create a new customer. */
export function useCreateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      code: string;
      name: string;
      type: "INDIVIDUAL" | "CORPORATE";
      currency_id: string;
      city_ref_id?: string;
      billing_address?: string;
      credit_limit_amount?: number | string;
      tax_number?: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/customers", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
  });
}
