import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Delete a customer by ID. */
export function useDeleteCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (customerId: string) => {
      const { error } = await apiClient.DELETE("/api/v1/customers/{customer_id}", {
        params: { path: { customer_id: customerId } },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
  });
}

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
      if (error) throw new Error(extractErrorMessage(error));
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
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!id,
  });
}

/** Update a customer by ID. */
export function useUpdateCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      customerId,
      ...body
    }: {
      customerId: string;
      name?: string;
      city_ref_id?: string;
      billing_address?: string;
      credit_limit_amount?: number | string;
      tax_number?: string;
      status?: string;
    }) => {
      const { data, error } = await apiClient.PATCH("/api/v1/customers/{customer_id}", {
        params: { path: { customer_id: customerId } },
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
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
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
  });
}
