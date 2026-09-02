/**
 * RepInventoryPage — read-only view of the representative's assigned
 * warehouse inventory balances.
 *
 * Shows:
 * - List of warehouses assigned to the current rep (GET /warehouses/my)
 * - Balance lookup per warehouse + product (GET /inventory/balance)
 *
 * No post-transaction or reverse actions — those remain office-only.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

interface Warehouse {
  id: string;
  code: string;
  name: string;
  type: string;
  status: string;
}

interface BalanceData {
  warehouse_id: string;
  product_id: string;
  lot_id: string | null;
  balance: string;
}

/** Fetch warehouses assigned to the current representative. */
function useMyWarehouses() {
  return useQuery({
    queryKey: ["warehouses", "my"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/warehouses/my", {
        headers: authHeader(),
      });
      if (error || !data) throw new Error(extractErrorMessage(error));
      return data.items as Warehouse[];
    },
  });
}

/** Fetch inventory balance for a warehouse + product. */
function useInventoryBalance(
  warehouseId: string,
  productId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["inventory-balance", warehouseId, productId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/inventory/balance", {
        params: {
          query: {
            warehouse_id: warehouseId,
            product_id: productId,
          },
        },
        headers: authHeader(),
      });
      if (error || !data) throw new Error(extractErrorMessage(error));
      return data as BalanceData;
    },
    enabled,
  });
}

export function RepInventoryPage() {
  const [selectedWarehouseId, setSelectedWarehouseId] = useState("");
  const [productId, setProductId] = useState("");

  const {
    data: warehouses,
    isLoading: warehousesLoading,
  } = useMyWarehouses();

  const {
    data: balance,
    isLoading: balanceLoading,
  } = useInventoryBalance(
    selectedWarehouseId,
    productId,
    !!selectedWarehouseId && !!productId,
  );

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">My Inventory</h1>

      {/* Warehouse selector */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          Assigned Warehouses
        </h2>

        {warehousesLoading ? (
          <p className="text-gray-500">Loading warehouses…</p>
        ) : (warehouses ?? []).length === 0 ? (
          <p className="text-gray-500">
            No warehouses are currently assigned to you.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(warehouses ?? []).map((wh) => (
              <button
                key={wh.id}
                onClick={() => setSelectedWarehouseId(wh.id)}
                className={`rounded-lg border p-4 text-left transition-colors ${
                  selectedWarehouseId === wh.id
                    ? "border-blue-500 bg-blue-50 ring-1 ring-blue-500"
                    : "border-gray-200 hover:border-gray-300 hover:bg-gray-50"
                }`}
              >
                <p className="font-medium text-gray-900">{wh.name}</p>
                <p className="text-sm text-gray-500">{wh.code}</p>
                <p className="mt-1 text-xs text-gray-400">
                  {wh.type} · {wh.status}
                </p>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Balance lookup */}
      {selectedWarehouseId && (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-gray-900">
            Balance Lookup
          </h2>
          <div className="flex items-end gap-4">
            <div className="flex-1">
              <label className="block text-sm font-medium text-gray-700">
                Product ID
              </label>
              <input
                type="text"
                value={productId}
                onChange={(e) => setProductId(e.target.value)}
                placeholder="Enter product UUID"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          {productId && (
            <div className="mt-4">
              {balanceLoading ? (
                <p className="text-gray-500">Loading balance…</p>
              ) : balance ? (
                <div className="rounded-md bg-gray-50 p-4">
                  <p className="text-sm text-gray-500">Balance</p>
                  <p className="mt-1 text-2xl font-bold text-gray-900">
                    {Number(balance.balance).toLocaleString()} units
                  </p>
                </div>
              ) : (
                <p className="text-gray-500">
                  No balance data for this product in the selected warehouse.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
