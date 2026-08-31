/**
 * RepCustomerListPage — read-only customer list for the Representative Portal.
 *
 * The backend already enforces representative scope server-side
 * (customer_service.list_customers filters by CustomerRepAssignment).
 * This page simply reuses the customer list hook but omits any
 * create/edit/deactivate actions (persona A4's responsibilities are
 * read-only for customers).
 *
 * Links to a read-only CustomerDetailView (reuses CustomerDetailPage's
 * display; edit/deactivate buttons are hidden via <Can> gating).
 */
import { useState } from "react";
import { Link } from "react-router";
import { useCustomers } from "@/api/hooks/useCustomers";
import { ROUTES } from "@/lib/constants";

const PAGE_SIZE = 50;

export function RepCustomerListPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>("");

  const { data: customers, isLoading, error } = useCustomers({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    search: search || undefined,
    status: statusFilter || undefined,
  });

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">My Customers</h1>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap gap-3">
        <input
          type="text"
          placeholder="Search by name, code, or tax number…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
          className="w-full max-w-md rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(0);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All statuses</option>
          <option value="ACTIVE">Active</option>
          <option value="INACTIVE">Inactive</option>
        </select>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}
      {error && <p className="text-red-600">Failed to load customers.</p>}

      {!isLoading && !error && (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Code
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Name
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Type
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Status
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    Credit Limit
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {(customers ?? []).length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="px-4 py-8 text-center text-sm text-gray-500"
                    >
                      No customers found.
                    </td>
                  </tr>
                ) : (
                  (customers ?? []).map((customer) => (
                    <tr key={customer.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <Link
                          to={`${ROUTES.REP}/customers/${customer.id}`}
                          className="font-medium text-blue-600 hover:underline"
                        >
                          {customer.code}
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                        {customer.name}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {customer.type}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            customer.status === "ACTIVE"
                              ? "bg-green-100 text-green-800"
                              : "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {customer.status}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
                        {Number(customer.credit_limit_amount).toLocaleString()}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Previous
            </button>
            <span className="text-sm text-gray-500">
              Page {page + 1}
            </span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(customers ?? []).length < PAGE_SIZE}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
