import { useParams, Link } from "react-router";
import { useProduct } from "@/api/hooks/useProducts";

export function ProductDetailPage() {
  const { sku } = useParams<{ sku: string }>();
  const { data: product, isLoading, error } = useProduct(sku ?? "");

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !product) {
    return (
      <div>
        <p className="text-red-600">Product not found.</p>
        <Link
          to="/office/catalog"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to products
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
          ← Products
        </Link>
      </div>

      <h1 className="mb-4 text-2xl font-bold text-gray-900">{product.name}</h1>

      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm font-medium text-gray-500">SKU</dt>
            <dd className="mt-1 text-sm text-gray-900">{product.sku}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Status</dt>
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
            <dt className="text-sm font-medium text-gray-500">Description</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.description ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Base UoM ID
            </dt>
            <dd className="mt-1 font-mono text-xs text-gray-600">
              {product.base_uom_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Category ID</dt>
            <dd className="mt-1 font-mono text-xs text-gray-600">
              {product.category_id ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Lot Tracked</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_lot_tracked ? "Yes" : "No"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Serial Tracked
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_serial_tracked ? "Yes" : "No"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Perishable</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {product.is_perishable ? "Yes" : "No"}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
