import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

export type PriceType =
  | "RETAIL"
  | "REP"
  | "WHOLESALE"
  | "EXPORT"
  | "PROMO";

interface NewPriceEntryInput {
  product_id: string;
  unit_price: number;
  effective_from: string;
  reason?: string | null;
  is_promo?: boolean;
  promo_valid_from?: string | null;
  promo_valid_to?: string | null;
}

/** List all price lists (admin/pricing screen). */
export function usePriceLists() {
  return useQuery({
    queryKey: ["price-lists"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/price-lists", {
        params: { query: { limit: 100 } },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Fetch a single price list by ID. */
export function usePriceList(priceListId: string) {
  return useQuery({
    queryKey: ["price-lists", priceListId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/price-lists/{price_list_id}",
        {
          params: { path: { price_list_id: priceListId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!priceListId,
  });
}

/** Create a new price list. */
export function useCreatePriceList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      name: string;
      price_type: PriceType;
      currency_id: string;
      owner_scope: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/price-lists", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["price-lists"] });
    },
  });
}

/** Rename / re-scope a price list (type & currency are structural). */
export function useUpdatePriceList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      priceListId,
      ...body
    }: {
      priceListId: string;
      name?: string;
      owner_scope?: string;
    }) => {
      const { data, error } = await apiClient.PATCH(
        "/api/v1/price-lists/{price_list_id}",
        {
          params: { path: { price_list_id: priceListId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["price-lists"] });
      queryClient.invalidateQueries({
        queryKey: ["price-lists", vars.priceListId],
      });
    },
  });
}

/** Toggle a price list active/inactive. */
export function useSetPriceListActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      priceListId,
      active,
    }: {
      priceListId: string;
      active: boolean;
    }) => {
      const { data, error } = await apiClient.POST(
        active
          ? "/api/v1/price-lists/{price_list_id}/activate"
          : "/api/v1/price-lists/{price_list_id}/deactivate",
        { params: { path: { price_list_id: priceListId } } as any, headers: authHeader() },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["price-lists"] });
      queryClient.invalidateQueries({
        queryKey: ["price-lists", vars.priceListId],
      });
    },
  });
}

/** Price entries (immutable price_history rows) of a price list. */
export function usePriceEntries(priceListId: string) {
  return useQuery({
    queryKey: ["price-lists", priceListId, "items"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/price-lists/{price_list_id}/items",
        {
          params: {
            path: { price_list_id: priceListId },
            query: { limit: 200 },
          },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    enabled: !!priceListId,
  });
}

/** Add a new price version for a product in this price list. */
export function useAddPriceEntry(priceListId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: NewPriceEntryInput) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/price-lists/{price_list_id}/items",
        {
          params: { path: { price_list_id: priceListId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["price-lists", priceListId, "items"],
      });
    },
  });
}

/**
 * Update a price: the backend creates a NEW immutable price_history version
 * (the previous version's effective_to is closed). Never overwrites history.
 */
export function useUpdatePriceEntry(priceListId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      entryId,
      ...body
    }: {
      entryId: string;
      unit_price: number;
      effective_from: string;
      reason?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/price-lists/{price_list_id}/items/{entry_id}/update-price",
        {
          params: {
            path: { price_list_id: priceListId, entry_id: entryId },
          },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["price-lists", priceListId, "items"],
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Customer price-list assignment
// ---------------------------------------------------------------------------

/** Price-list assignments for one customer (current + historical). */
export function useCustomerPriceLists(customerId: string) {
  return useQuery({
    queryKey: ["customer-price-lists", customerId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/customers/{customer_id}/price-lists",
        {
          params: { path: { customer_id: customerId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    enabled: !!customerId,
  });
}

/** Assign a price list to a customer (with priority + effective window). */
export function useAssignCustomerPriceList(customerId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      price_list_id: string;
      effective_from: string;
      effective_to?: string | null;
      priority: number;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/customers/{customer_id}/price-lists",
        {
          params: { path: { customer_id: customerId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["customer-price-lists", customerId],
      });
    },
  });
}
