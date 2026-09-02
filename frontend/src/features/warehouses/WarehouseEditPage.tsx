import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useWarehouses, useUpdateWarehouse } from "@/api/hooks/useWarehouses";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function WarehouseEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateWarehouse = useUpdateWarehouse();
  const canManage = usePermission(PERMISSIONS.WAREHOUSE_MANAGE);

  const { data: warehouses, isLoading } = useWarehouses();
  const warehouse = (warehouses ?? []).find((w: any) => w.id === id);

  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (warehouse) {
      setName(warehouse.name);
      setAddress(warehouse.address ?? "");
      setStatus(warehouse.status);
    }
  }, [warehouse]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("warehouses.noPermission")}</p>
        <Link to="/office/warehouses" className="mt-4 inline-block text-sm text-blue-600 hover:underline">{t("warehouses.backToWarehouses")}</Link>
      </div>
    );
  }

  if (isLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!warehouse) return <p className="text-red-600">{t("warehouses.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateWarehouse.mutateAsync({
        warehouseId: id!,
        name,
        address: address || undefined,
        status,
      });
      navigate("/office/warehouses", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("warehouses.failedToDelete"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/warehouses" className="text-sm text-blue-600 hover:underline">← {t("warehouses.title")}</Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("warehouses.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("warehouses.fields.code")}</label>
          <input type="text" value={warehouse.code} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">{t("warehouses.fields.name")}</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="address" className="block text-sm font-medium text-gray-700">{t("warehouses.fields.address")}</label>
          <textarea id="address" value={address} onChange={(e) => setAddress(e.target.value)} maxLength={255} rows={2}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="status" className="block text-sm font-medium text-gray-700">{t("warehouses.columns.status")}</label>
          <select id="status" value={status} onChange={(e) => setStatus(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="ACTIVE">ACTIVE</option>
            <option value="INACTIVE">INACTIVE</option>
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateWarehouse.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateWarehouse.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
