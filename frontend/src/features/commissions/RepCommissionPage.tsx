/**
 * RepCommissionPage — dedicated page showing commission balance
 * and transaction history for the logged-in representative.
 *
 * Balance comes from GET /representatives/{id}/commission-balance.
 * Transaction history comes from GET /commission-transactions?representative_id=...
 * (server-side scoped to the caller's representative for rep-linked users).
 */
import { useState } from "react";
import {
  useCommissionBalance,
  useCommissionTransactions,
} from "@/api/hooks/useCommissions";

const STATE_BADGE: Record<string, string> = {
  ACCRUED: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  PAID: "bg-green-100 text-green-800",
  CLAWED_BACK: "bg-red-100 text-red-800",
};

const PAGE_SIZE = 20;

export function RepCommissionPage() {
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");

  // We need the representative_id to query balance.
  // The commission-transactions endpoint is server-side scoped,
  // so we can list transactions without knowing the rep ID.
  // For balance, we need the ID. We'll fetch it from transactions
  // or use a derived approach.

  // Fetch transactions first (server-side scoped) to get representative_id.
  const {
    data: transactions,
    isLoading: txnsLoading,
  } = useCommissionTransactions({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    state_event: stateFilter || undefined,
  });

  // Derive representative_id from first transaction.
  const representativeId =
    transactions?.[0]?.representative_id ?? "";

  const {
    data: balanceData,
    isLoading: balanceLoading,
  } = useCommissionBalance(representativeId);

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">My Commission</h1>

      {/* Balance Card */}
      <div className="mb-8 rounded-lg border border-gray-200 bg-white p-6">
        <h2 className="text-sm font-medium text-gray-500">
          Net Commission Balance
        </h2>
        <p className="mt-2 text-4xl font-bold text-gray-900">
          {balanceLoading
            ? "…"
            : balanceData
              ? Number(balanceData.balance).toLocaleString()
              : "0.00"}
        </p>
        <p className="mt-1 text-xs text-gray-500">
          Computed as the sum of all commission transactions (ACCRUED + APPROVED −
          CLAWED_BACK − PAID).
        </p>
      </div>

      {/* Transaction History */}
      <div>
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Transaction History
        </h2>

        {/* Filter */}
        <div className="mb-4">
          <select
            value={stateFilter}
            onChange={(e) => {
              setStateFilter(e.target.value);
              setPage(0);
            }}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value="">All states</option>
            <option value="ACCRUED">Accrued</option>
            <option value="APPROVED">Approved</option>
            <option value="PAID">Paid</option>
            <option value="CLAWED_BACK">Clawed Back</option>
          </select>
        </div>

        {txnsLoading ? (
          <p className="text-gray-500">Loading…</p>
        ) : (transactions ?? []).length === 0 ? (
          <p className="text-gray-500">No commission transactions yet.</p>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Date
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      State
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                      Amount
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                      Rate
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Order
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {transactions!.map((txn) => (
                    <tr key={txn.id} className="hover:bg-gray-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
                        {new Date(txn.occurred_at).toLocaleDateString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                            STATE_BADGE[txn.state_event] ??
                            "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {txn.state_event.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                        {Number(txn.signed_amount).toLocaleString()}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right text-sm text-gray-500">
                        {Number(txn.rate_applied)}%
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                        {txn.order_id
                          ? txn.order_id.slice(0, 8) + "…"
                          : "—"}
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
                disabled={(transactions ?? []).length < PAGE_SIZE}
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
