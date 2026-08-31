import { type FormEvent, useState } from "react";
import { useNavigate, Link } from "react-router";
import { useCreateProduct } from "@/api/hooks/useProducts";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

export function ProductCreatePage() {
  const navigate = useNavigate();
  const createProduct = useCreateProduct();
  const canManage = usePermission(PERMISSIONS.PRODUCT_MANAGE);

  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [baseUomId, setBaseUomId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!canManage) {
    return (
      <div>
        <p className="text-red-600">
          You do not have permission to create products.
        </p>
        <Link
          to="/office/catalog"
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to products
        </Link>
      </div>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    try {
      await createProduct.mutateAsync({
        sku,
        name,
        description: description || undefined,
        base_uom_id: baseUomId,
        category_id: categoryId || undefined,
      });
      navigate(`/office/catalog/${sku}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create product");
    }
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

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Create Product
      </h1>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4 rounded-lg border border-gray-200 bg-white p-6"
      >
        <div>
          <label htmlFor="sku" className="block text-sm font-medium text-gray-700">
            SKU *
          </label>
          <input
            id="sku"
            type="text"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            required
            maxLength={40}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="name" className="block text-sm font-medium text-gray-700">
            Name *
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
        </div>

        <div>
          <label htmlFor="description" className="block text-sm font-medium text-gray-700">
            Description
          </label>
          <textarea
            id="description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={255}
            rows={3}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="base_uom_id" className="block text-sm font-medium text-gray-700">
            Base UoM ID *
          </label>
          <input
            id="base_uom_id"
            type="text"
            value={baseUomId}
            onChange={(e) => setBaseUomId(e.target.value)}
            required
            placeholder="UUID"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div>
          <label htmlFor="category_id" className="block text-sm font-medium text-gray-700">
            Category ID
          </label>
          <input
            id="category_id"
            type="text"
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            placeholder="UUID (optional)"
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createProduct.isPending}
          className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50"
        >
          {createProduct.isPending ? "Creating…" : "Create Product"}
        </button>
      </form>
    </div>
  );
}
