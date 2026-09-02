import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// List transactions
// ---------------------------------------------------------------------------

interface ListTransactionsParams {
  warehouse_id: string;
  product_id?: string;
  lot_id?: string;
  skip?: number;
  limit?: number;
}

/** Fetch raw ledger rows for a warehouse, optionally filtered by product/lot. */
export function useInventoryTransactions(params: ListTransactionsParams) {
  const { warehouse_id, product_id, lot_id, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: [
      "inventory-transactions",
      { warehouse_id, product_id, lot_id, skip, limit },
    ],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/inventory/transactions" as any,
        {
          params: {
            query: {
              warehouse_id,
              ...(product_id ? { product_id } : {}),
              ...(lot_id ? { lot_id } : {}),
              skip,
              limit,
            },
          },
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items;
    },
    enabled: !!warehouse_id,
  });
}

// ---------------------------------------------------------------------------
// Get balance
// ---------------------------------------------------------------------------

/** Fetch the current live-computed balance for (warehouse, product, lot). */
export function useInventoryBalance(
  warehouse_id: string,
  product_id: string,
  lot_id?: string,
) {
  return useQuery({
    queryKey: ["inventory-balance", { warehouse_id, product_id, lot_id }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/inventory/balance", {
        params: {
          query: {
            warehouse_id,
            product_id,
            ...(lot_id ? { lot_id } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!warehouse_id && !!product_id,
  });
}

// ---------------------------------------------------------------------------
// Post transaction
// ---------------------------------------------------------------------------

/** Post a new inventory ledger transaction. */
export function usePostTransaction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      product_id: string;
      warehouse_id: string;
      movement_type_code: string;
      signed_quantity: number | string;
      unit_cost: number | string;
      currency_id: string;
      lot_id?: string | null;
      reason_code_id?: string | null;
      reference_type?: string | null;
      reference_id?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/inventory/transactions",
        {
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory-transactions"] });
      queryClient.invalidateQueries({ queryKey: ["inventory-balance"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Reverse transaction
// ---------------------------------------------------------------------------

/** Reverse a previously posted ledger transaction. */
export function useReverseTransaction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      transactionId,
      reason_code_id,
    }: {
      transactionId: string;
      reason_code_id?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/inventory/transactions/{transaction_id}/reverse",
        {
          params: { path: { transaction_id: transactionId } },
          body: { reason_code_id } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory-transactions"] });
      queryClient.invalidateQueries({ queryKey: ["inventory-balance"] });
    },
  });
}

// ---------------------------------------------------------------------------
// List movement types
// ---------------------------------------------------------------------------

/** Fetch the seeded movement type catalog (code, label, sign). */
export function useMovementTypes() {
  return useQuery({
    queryKey: ["movement-types"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/movement-types" as any,
        {
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items;
    },
  });
}
