import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateWarehouse } from "@/api/hooks/useWarehouses";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function WarehouseCreatePage() {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const createWarehouse = useCreateWarehouse();
  const canManage = usePermission(PERMISSIONS.WAREHOUSE_MANAGE);

  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<"FACTORY" | "REPRESENTATIVE">("FACTORY");
  const [ownershipMode, setOwnershipMode] = useState<"OWNED" | "CONSIGNMENT">("OWNED");
  const [address, setAddress] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          {t("warehouses.noPermission")}
        </p>
        <Link
          to="/office/warehouses"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("warehouses.backToWarehouses")}
        </Link>
      </div>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    try {
      await createWarehouse.mutateAsync({
        code,
        name,
        type,
        ownership_mode: ownershipMode,
        address: address || undefined,
      });
      navigate("/office/warehouses", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("warehouses.failedToCreate"),
      );
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/warehouses"
          className="text-sm text-blue-600 hover:underline"
        >
          {t("warehouses.backToWarehouses")}
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        {t("warehouses.createTitle")}
      </h1>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6"
      >
        <div>
          <label htmlFor="code" className="block text-sm font-medium text-gray-700">
            {t("warehouses.fields.code")}
          </label>
          <input
            id="code"
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-500">
            {t("warehouses.fields.codeHelp")}
          </p>
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">
            {t("warehouses.fields.name")}
          </label>
          <input
            id="name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <p className="mt-1 text-xs text-gray-500">
            {t("warehouses.fields.nameHelp")}
          </p>
        </div>

        <div>
          <label htmlFor="type" className="block text-sm font-medium text-gray-700">
            {t("warehouses.fields.type")}
          </label>
          <select
            id="type"
            value={type}
            onChange={(e) => setType(e.target.value as "FACTORY" | "REPRESENTATIVE")}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="FACTORY">{t("warehouses.typeOptions.FACTORY")}</option>
            <option value="REPRESENTATIVE">{t("warehouses.typeOptions.REPRESENTATIVE")}</option>
          </select>
        </div>

        <div>
          <label htmlFor="ownership_mode" className="block text-sm font-medium text-gray-700">
            {t("warehouses.fields.ownership")}
          </label>
          <select
            id="ownership_mode"
            value={ownershipMode}
            onChange={(e) => setOwnershipMode(e.target.value as "OWNED" | "CONSIGNMENT")}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="OWNED">{t("warehouses.ownershipOptions.OWNED")}</option>
            <option value="CONSIGNMENT">{t("warehouses.ownershipOptions.CONSIGNMENT")}</option>
          </select>
        </div>

        <div>
          <label htmlFor="address" className="block text-sm font-medium text-gray-700">
            {t("warehouses.fields.address")}
          </label>
          <textarea
            id="address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            maxLength={255}
            rows={2}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createWarehouse.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createWarehouse.isPending ? t("warehouses.buttons.creating") : t("warehouses.buttons.create")}
        </button>
      </form>
    </div>
  );
}
