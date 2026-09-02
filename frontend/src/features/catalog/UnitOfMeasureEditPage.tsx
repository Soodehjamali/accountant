import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useUnitsOfMeasure, useUpdateUnitOfMeasure } from "@/api/hooks/useUnitsOfMeasure";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function UnitOfMeasureEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateUom = useUpdateUnitOfMeasure();
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);

  const { data: uomList, isLoading } = useUnitsOfMeasure();
  const uom = (uomList ?? []).find((u: any) => u.id === id);

  const [name, setName] = useState("");
  const [class_, setClass_] = useState("BASE");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (uom) {
      setName(uom.name);
      setClass_(uom.class_);
    }
  }, [uom]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("catalog.noPermission")}</p>
        <Link to="/office/catalog/uom" className="mt-4 inline-block text-sm text-blue-600 hover:underline">{t("catalog.uom.title")}</Link>
      </div>
    );
  }

  if (isLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!uom) return <p className="text-red-600">{t("catalog.uom.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateUom.mutateAsync({
        uomId: id!,
        name,
        class_,
      });
      navigate("/office/catalog/uom", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("catalog.uom.failedToDelete"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/catalog/uom" className="text-sm text-blue-600 hover:underline">← {t("catalog.uom.title")}</Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("catalog.uom.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("catalog.uom.columns.code")}</label>
          <input type="text" value={uom.code} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">{t("catalog.uom.columns.name")}</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="class" className="block text-sm font-medium text-gray-700">{t("catalog.uom.columns.class")}</label>
          <select id="class" value={class_} onChange={(e) => setClass_(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="BASE">BASE</option>
            <option value="DERIVED">DERIVED</option>
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateUom.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateUom.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
