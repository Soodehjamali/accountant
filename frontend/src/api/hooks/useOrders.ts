import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

// ---------------------------------------------------------------------------
// List / Read
// ---------------------------------------------------------------------------

interface ListOrdersParams {
  skip?: number;
  limit?: number;
  state?: string;
  representative_id?: string;
  customer_id?: string;
}

/** Fetch orders with pagination and optional state/representative filters. */
export function useOrders(params: ListOrdersParams = {}) {
  const { skip = 0, limit = 50, state, representative_id, customer_id } = params;
  return useQuery({
    queryKey: ["orders", { skip, limit, state, representative_id, customer_id }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/orders", {
        params: {
          query: {
            skip,
            limit,
            ...(state ? { state: state as any } : {}),
            ...(representative_id ? { representative_id } : {}),
            ...(customer_id ? { customer_id } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data.items;
    },
  });
}

/** Fetch a single order by ID (includes lines). */
export function useOrder(id: string) {
  return useQuery({
    queryKey: ["orders", id],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/orders/{order_id}",
        {
          params: { path: { order_id: id } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    enabled: !!id,
  });
}

/** Fetch an order's status-transition history. */
export function useOrderHistory(orderId: string) {
  return useQuery({
    queryKey: ["orders", orderId, "history"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/orders/{order_id}/history",
        {
          params: { path: { order_id: orderId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data.items;
    },
    enabled: !!orderId,
  });
}

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/** Create a new draft order. */
export function useCreateOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      customer_id: string;
      representative_id: string;
      currency_id: string;
      price_list_id?: string | null;
      order_type: "LOCAL" | "DIRECT";
      fulfillment_mode: "REP_LOCAL" | "FACTORY_DIRECT";
      sales_channel: string;
      lines: Array<{
        product_id: string;
        fulfillment_warehouse_id: string;
        qty_ordered: number | string;
        fulfillment_mode: "REP_LOCAL" | "FACTORY_DIRECT";
        price_history_id?: string | null;
        lot_id?: string | null;
        discount_id?: string | null;
        discount_value?: number | string;
      }>;
      customer_city_ref_id?: string | null;
      rep_city_ref_id?: string | null;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/orders", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Line editing (DRAFT only)
// ---------------------------------------------------------------------------

/** Add a line to a DRAFT order. */
export function useAddOrderLine(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      product_id: string;
      fulfillment_warehouse_id: string;
      qty_ordered: number | string;
      fulfillment_mode: "REP_LOCAL" | "FACTORY_DIRECT";
      price_history_id?: string | null;
      lot_id?: string | null;
      discount_id?: string | null;
      discount_value?: number | string;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/orders/{order_id}/lines",
        {
          params: { path: { order_id: orderId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
    },
  });
}

/** Remove a line from a DRAFT order. */
export function useRemoveOrderLine(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (lineId: string) => {
      const { error } = await apiClient.DELETE(
        "/api/v1/orders/{order_id}/lines/{line_id}",
        {
          params: { path: { order_id: orderId, line_id: lineId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
    },
  });
}

/** Update quantity on a DRAFT order line. */
export function useUpdateOrderLineQty(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      lineId,
      qty_ordered,
    }: {
      lineId: string;
      qty_ordered: number | string;
    }) => {
      const { data, error } = await apiClient.PATCH(
        "/api/v1/orders/{order_id}/lines/{line_id}",
        {
          params: { path: { order_id: orderId, line_id: lineId } },
          body: { qty_ordered },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
    },
  });
}

/** Override price on a DRAFT order line. */
export function useUpdateOrderLinePrice(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      lineId,
      unit_price,
    }: {
      lineId: string;
      unit_price: number | string;
    }) => {
      const { data, error } = await apiClient.PATCH(
        "/api/v1/orders/{order_id}/lines/{line_id}/price",
        {
          params: { path: { order_id: orderId, line_id: lineId } },
          body: { unit_price },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
    },
  });
}

// ---------------------------------------------------------------------------
// State transitions
// ---------------------------------------------------------------------------

interface TransitionResult {
  orderId: string;
  note?: string | null;
}

function useOrderTransition(
  pathSuffix: string,
  _method: "POST",
  _permissionKey: string,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ orderId, note }: TransitionResult) => {
      const { data, error } = await apiClient.POST(
        `/api/v1/orders/{order_id}/${pathSuffix}` as any,
        {
          params: { path: { order_id: orderId } },
          body: { note: note ?? undefined } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({
        queryKey: ["orders", variables.orderId],
      });
      queryClient.invalidateQueries({
        queryKey: ["orders", variables.orderId, "history"],
      });
    },
  });
}

export function useSubmitOrder() {
  return useOrderTransition("submit", "POST", "ORDER_MANAGE");
}

export function useApproveOrder() {
  return useOrderTransition("approve", "POST", "ORDER_APPROVE");
}

export function useReserveOrder() {
  return useOrderTransition("reserve", "POST", "ORDER_MANAGE");
}

export function useResubmitOrder() {
  return useOrderTransition("resubmit", "POST", "ORDER_MANAGE");
}

export function useCancelOrder() {
  return useOrderTransition("cancel", "POST", "ORDER_MANAGE");
}

export function useStartFulfillment() {
  return useOrderTransition("start-fulfillment", "POST", "ORDER_MANAGE");
}

export function useRecordReturn() {
  return useOrderTransition("return", "POST", "ORDER_MANAGE");
}

export function useMarkInvoiced() {
  return useOrderTransition("invoice", "POST", "ORDER_MANAGE");
}

export function useMarkCompleted() {
  return useOrderTransition("complete", "POST", "ORDER_MANAGE");
}

/** Ship an order (requires ShipOrderRequest body). */
export function useShipOrder(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (
      lines: Array<{ order_line_id: string; quantity: number | string }>,
    ) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/orders/{order_id}/ship",
        {
          params: { path: { order_id: orderId } },
          body: { lines } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
      queryClient.invalidateQueries({
        queryKey: ["orders", orderId, "history"],
      });
    },
  });
}

/** Mark an order as paid (requires OrderPaymentRequest body). */
export function useMarkPaid(orderId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      amount: number | string;
      method: string;
      reference?: string | null;
      note?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/orders/{order_id}/pay",
        {
          params: { path: { order_id: orderId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["orders", orderId] });
      queryClient.invalidateQueries({
        queryKey: ["orders", orderId, "history"],
      });
    },
  });
}
