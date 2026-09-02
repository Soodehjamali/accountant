import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// List / Read
// ---------------------------------------------------------------------------

interface ListTransfersParams {
  skip?: number;
  limit?: number;
  state?: string;
  source_warehouse_id?: string;
  destination_warehouse_id?: string;
}

/** Fetch stock transfers with pagination and optional filters. */
export function useTransfers(params: ListTransfersParams = {}) {
  const {
    skip = 0,
    limit = 50,
    state,
    source_warehouse_id,
    destination_warehouse_id,
  } = params;
  return useQuery({
    queryKey: [
      "transfers",
      { skip, limit, state, source_warehouse_id, destination_warehouse_id },
    ],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/transfers", {
        params: {
          query: {
            skip,
            limit,
            ...(state ? { state: state as any } : {}),
            ...(source_warehouse_id ? { source_warehouse_id } : {}),
            ...(destination_warehouse_id ? { destination_warehouse_id } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Fetch a single stock transfer by ID (includes lines). */
export function useTransfer(id: string) {
  return useQuery({
    queryKey: ["transfers", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/transfers/{transfer_id}",
        {
          params: { path: { transfer_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    enabled: !!id,
  });
}

/** Fetch a transfer's state-transition history. */
export function useTransferHistory(transferId: string) {
  return useQuery({
    queryKey: ["transfers", transferId, "history"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/transfers/{transfer_id}/history",
        {
          params: { path: { transfer_id: transferId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
    enabled: !!transferId,
  });
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/** Create a new draft stock transfer. */
export function useCreateTransfer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      source_warehouse_id: string;
      destination_warehouse_id: string;
      lines: Array<{
        product_id: string;
        qty_requested: number | string;
        unit_cost: number | string;
        lot_id?: string | null;
      }>;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/transfers", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["transfers"] });
    },
  });
}

// ---------------------------------------------------------------------------
// State transitions
// ---------------------------------------------------------------------------

function useTransferTransition(pathSuffix: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ transferId, note }: { transferId: string; note?: string | null }) => {
      const { data, error } = await apiClient.POST(
        `/api/v1/transfers/{transfer_id}/${pathSuffix}` as any,
        {
          params: { path: { transfer_id: transferId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["transfers"] });
      queryClient.invalidateQueries({ queryKey: ["transfers", variables.transferId] });
      queryClient.invalidateQueries({
        queryKey: ["transfers", variables.transferId, "history"],
      });
    },
  });
}

export function useSubmitTransfer() {
  return useTransferTransition("submit");
}

export function useApproveTransfer() {
  return useTransferTransition("approve");
}

export function useDispatchTransfer() {
  return useTransferTransition("dispatch");
}

export function useReceiveTransfer() {
  return useTransferTransition("receive");
}

export function useCancelTransfer() {
  return useTransferTransition("cancel");
}
