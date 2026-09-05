import { useState } from "react";
import { useParams, Link } from "react-router";
import { useCustomer } from "@/api/hooks/useCustomers";
import { useCustomerBalance, useCustomerLedgerEntries } from "@/api/hooks/useCustomerLedger";
import { CustomerPriceListSection } from "@/features/price-lists/CustomerPriceListSection";

const ENTRY_TYPE_BADGE: Record<string, string> = {
  INVOICE_ISSUED: "bg-blue-100 text-blue-800",
  PAYMENT_RECEIVED: "bg-green-100 text-green-800",
  CREDIT_NOTE_APPLIED: "bg-purple-100 text-purple-800",
  WRITE_OFF: "bg-red-100 text-red-800",
};

const PAGE_SIZE = 25;

export function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: customer, isLoading, error } = useCustomer(id ?? "");
  const { data: balance } = useCustomerBalance(id ?? "");
  const [page, setPage] = useState(0);
  const [entryType, setEntryType] = useState("");

  const { data: entries, isLoading: entriesLoading } = useCustomerLedgerEntries(id ?? "", {
    entry_type: entryType || undefined,
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !customer) {
    return (
      <div>
        <p className="text-red-600">Customer not found.</p>
        <Link
          to="/office/customers"
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
          to="/office/customers"
          className="text-sm text-blue-600 hover:underline"
        >
          ← Customers
        </Link>
      </div>

      <h1 className="mb-4 text-2xl font-bold text-gray-900">
        {customer.name}
      </h1>

      {/* Customer info */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
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

      {/* Price-list assignment (customer-specific pricing, BR-P1) */}
      <CustomerPriceListSection customerId={customer.id} />

      {/* Balance card */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <h2 className="text-sm font-medium text-gray-500">
          Accounts Receivable Balance
        </h2>
        <p className="mt-2 text-4xl font-bold text-gray-900">
          {balance != null ? Number(balance.balance).toLocaleString() : "0.00"}
        </p>
        <p className="mt-1 text-xs text-gray-500">
          Computed live from the customer ledger (not cached)
        </p>
      </div>

      {/* Ledger entries */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">
            Ledger Entries
          </h2>
          <select
            value={entryType}
            onChange={(e) => {
              setEntryType(e.target.value);
              setPage(0);
            }}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">All types</option>
            <option value="INVOICE_ISSUED">Invoice Issued</option>
            <option value="PAYMENT_RECEIVED">Payment Received</option>
            <option value="CREDIT_NOTE_APPLIED">Credit Note Applied</option>
            <option value="WRITE_OFF">Write Off</option>
          </select>
        </div>

        {entriesLoading ? (
          <p className="text-gray-500">Loading…</p>
        ) : (entries ?? []).length === 0 ? (
          <div className="rounded-lg border border-gray-200 bg-white p-6">
            <p className="text-sm text-gray-500">No ledger entries yet.</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Seq
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Type
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                      Amount
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Reference
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Date
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {entries!.map((entry: any) => (
                    <tr key={entry.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-mono text-gray-700">
                        {entry.sequence_no}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            ENTRY_TYPE_BADGE[entry.entry_type] ??
                            "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {entry.entry_type?.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td
                        className={`whitespace-nowrap px-4 py-3 text-right text-sm font-medium ${
                          Number(entry.signed_amount) >= 0
                            ? "text-red-700"
                            : "text-green-700"
                        }`}
                      >
                        {Number(entry.signed_amount) > 0 ? "+" : ""}
                        {Number(entry.signed_amount).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                        {entry.reference_type && (
                          <span>
                            {entry.reference_type}/
                            {entry.reference_id?.slice(0, 8)}
                          </span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {new Date(entry.occurred_at).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
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
              <span className="text-sm text-gray-500">Page {page + 1}</span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={(entries ?? []).length < PAGE_SIZE}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
