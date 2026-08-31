/**
 * RepCustomerDetailPage — read-only customer detail for the rep portal.
 *
 * Reuses CustomerDetailPage's display component. Edit/deactivate buttons
 * are not present (CustomerDetailPage has none — it's already read-only).
 * The only difference is the back link points to /rep/customers.
 */
import { useParams, Link } from "react-router";
import { useCustomer } from "@/api/hooks/useCustomers";
import { ROUTES } from "@/lib/constants";

export function RepCustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: customer, isLoading, error } = useCustomer(id ?? "");

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !customer) {
    return (
      <div>
        <p className="text-red-600">Customer not found.</p>
        <Link
          to={`${ROUTES.REP}/customers`}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to customers
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={`${ROUTES.REP}/customers`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Customers
        </Link>
      </div>

      <h1 className="mb-4 text-2xl font-bold text-gray-900">
        {customer.name}
      </h1>

      <div className="rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm font-medium text-gray-500">Code</dt>
            <dd className="mt-1 text-sm font-medium text-gray-900">
              {customer.code}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Status</dt>
            <dd className="mt-1">
              <span
                className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                  customer.status === "ACTIVE"
                    ? "bg-green-100 text-green-800"
                    : "bg-gray-100 text-gray-800"
                }`}
              >
                {customer.status}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Type</dt>
            <dd className="mt-1 text-sm text-gray-900">{customer.type}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Credit Limit
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(customer.credit_limit_amount).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Tax Number</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {customer.tax_number ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Currency ID
            </dt>
            <dd className="mt-1 font-mono text-xs text-gray-600">
              {customer.currency_id}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-sm font-medium text-gray-500">
              Billing Address
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {customer.billing_address ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Created</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(customer.created_at).toLocaleDateString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Updated</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(customer.updated_at).toLocaleDateString()}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
