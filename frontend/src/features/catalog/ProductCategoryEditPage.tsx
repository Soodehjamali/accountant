import { type FormEvent, useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useProductCategories, useUpdateProductCategory } from "@/api/hooks/useProductCategories";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function ProductCategoryEditPage() {
  const { t } = useTranslation("common");
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const updateCategory = useUpdateProductCategory();
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);

  const { data: categories, isLoading: catLoading } = useProductCategories();
  const category = (categories ?? []).find((c: any) => c.id === id);

  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (category) {
      setName(category.name);
      setParentId(category.parent_category_id ?? "");
    }
  }, [category]);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">{t("catalog.noPermission")}</p>
        <Link to="/office/catalog/categories" className="mt-4 inline-block text-sm text-blue-600 hover:underline">{t("catalog.backToProducts")}</Link>
      </div>
    );
  }

  if (catLoading) return <p className="text-gray-500">{t("status.loading")}</p>;
  if (!category) return <p className="text-red-600">{t("catalog.categories.failedToLoad")}</p>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await updateCategory.mutateAsync({
        categoryId: id!,
        name,
        parent_category_id: parentId || undefined,
      });
      navigate("/office/catalog/categories", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("catalog.categories.failedToDelete"));
    }
  }

  return (
    <div>
      <div className="mb-6">
        <Link to="/office/catalog/categories" className="text-sm text-blue-600 hover:underline">← {t("catalog.categories.title")}</Link>
      </div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">{t("catalog.categories.editTitle")}</h1>

      <form onSubmit={handleSubmit} className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <div>
          <label className="block text-sm font-medium text-gray-700">{t("catalog.categories.columns.code")}</label>
          <input type="text" value={category.code} disabled className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">{t("catalog.categories.columns.name")}</label>
          <input id="name" type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={160}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        </div>

        <div>
          <label htmlFor="parent" className="block text-sm font-medium text-gray-700">{t("catalog.categories.parentCategory")}</label>
          <select id="parent" value={parentId} onChange={(e) => setParentId(e.target.value)}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
            <option value="">{t("forms.selectPlaceholder")}</option>
            {(categories ?? []).filter((c: any) => c.id !== id).map((c: any) => (
              <option key={c.id} value={c.id}>{c.name} ({c.code})</option>
            ))}
          </select>
        </div>

        {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

        <button type="submit" disabled={updateCategory.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50">
          {updateCategory.isPending ? t("catalog.buttons.creating") : t("catalog.buttons.update")}
        </button>
      </form>
    </div>
  );
}
