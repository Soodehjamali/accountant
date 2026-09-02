import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useProduct, useUpdateProduct } from "@/api/hooks/useProducts";
import { useUnitsOfMeasure } from "@/api/hooks/useUnitsOfMeasure";
import { useProductCategories } from "@/api/hooks/useProductCategories";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function ProductEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateProduct = useUpdateProduct();
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);

  const { data: product, isLoading: productLoading } = useProduct(id ?? "");
  const { data: uomList, isLoading: uomLoading } = useUnitsOfMeasure();
  const { data: categoryList, isLoading: catLoading } = useProductCategories();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [baseUomId, setBaseUomId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [status, setStatus] = useState("ACTIVE");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (product) {
      setName(product.name);
      setDescription(product.description ?? "");
      setBaseUomId(product.base_uom_id);
      setCategoryId(product.category_id ?? "");
      setStatus(product.status);
    }
  }, [product]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("catalog.noPermission")}</p>
        <Link to="/office/catalog" className="mt-4 inline-block text-sm text-blue-600 hover:underline">
          {t("catalog.backToProducts")}
        </Link>
      </div>
    );
  }

  if (productLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!product) return <p className="text-red-600">{t("catalog.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateProduct.mutateAsync({
        productId: product.id,
        name,
        description: description || undefined,
        base_uom_id: baseUomId,
        category_id: categoryId || undefined,
        status,
      });
      navigate(`/office/catalog/${product.sku}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("catalog.failedToCreate"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/catalog" className="text-sm text-blue-600 hover:underline">
          {t("catalog.backToProducts")}
        </Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("catalog.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("catalog.fields.sku")}</label>
          <input type="text" value={product.sku} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">{t("catalog.fields.name")}</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="description" className="block text-sm font-medium text-gray-700">{t("catalog.fields.description")}</label>
          <textarea id="description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={255} rows={3}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="base_uom_id" className="block text-sm font-medium text-gray-700">{t("catalog.fields.baseUomId")}</label>
          <select id="base_uom_id" value={baseUomId} onChange={(e) => setBaseUomId(e.target.value)} required disabled={uomLoading}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="">{uomLoading ? t("common.loading") : t("forms.selectPlaceholder")}</option>
            {(uomList ?? []).map((uom) => (
              <option key={uom.id} value={uom.id}>{uom.name} ({uom.code})</option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="category_id" className="block text-sm font-medium text-gray-700">{t("catalog.fields.categoryId")}</label>
          <select id="category_id" value={categoryId} onChange={(e) => setCategoryId(e.target.value)} disabled={catLoading}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="">{catLoading ? t("common.loading") : t("forms.selectPlaceholder")}</option>
            {(categoryList ?? []).map((cat) => (
              <option key={cat.id} value={cat.id}>{cat.name} ({cat.code})</option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="status" className="block text-sm font-medium text-gray-700">{t("catalog.columns.status")}</label>
          <select id="status" value={status} onChange={(e) => setStatus(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="ACTIVE">ACTIVE</option>
            <option value="DISCONTINUED">DISCONTINUED</option>
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateProduct.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateProduct.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
