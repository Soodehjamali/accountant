import { useParams, Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useProduct } from "@/api/hooks/useProducts";
import { useUnitsOfMeasure } from "@/api/hooks/useUnitsOfMeasure";

export function ProductDetailPage() {
  const { t } = useTranslation("common");
  const { sku } = useParams<{ sku: string }>();
  const { data: product, isLoading, error } = useProduct(sku ?? "");
  const { data: uomList } = useUnitsOfMeasure();

  // Build a lookup map from UoM ID to name
  const uomMap = new Map<string, string>();
  for (const uom of uomList ?? []) {
    uomMap.set(uom.id, uom.name);
  }

  if (isLoading) {
    return <p className="text-gray-500">{t("status.loading")}</p>;
  }

  if (error || !product) {
    return (
      <div>
        <p className="text-red-600">{t("catalog.failedToLoad")}</p>
        <Link
          to="/office/catalog"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          {t("catalog.backToProducts")}
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to="/office/catalog"
          className="text-sm text-blue-600 hover:underline"
        >
          {t("catalog.backToProducts")}
        </Link>
      </div>

      <h1 className="mb-4 text-2xl font-bold text-gray-900">{product.name}</h1>

      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.columns.sku")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">{product.sku}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.columns.status")}
            </dt>
            <dd className="mt-1">
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                  product.status === "ACTIVE"
                    ? "bg-green-100 text-green-800"
                    : "bg-gray-100 text-gray-800"
                }`}
              >
                {product.status}
              </span>
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.fields.description")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.description ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.fields.baseUomId")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {uomMap.get(product.base_uom_id) ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.fields.categoryId")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.category_id ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.columns.lotTracked")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_lot_tracked
                ? t("catalog.lotTracked.yes")
                : t("catalog.lotTracked.no")}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.fields.serialTracked")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_serial_tracked
                ? t("catalog.lotTracked.yes")
                : t("catalog.lotTracked.no")}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              {t("catalog.fields.perishable")}
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_perishable
                ? t("catalog.lotTracked.yes")
                : t("catalog.lotTracked.no")}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
