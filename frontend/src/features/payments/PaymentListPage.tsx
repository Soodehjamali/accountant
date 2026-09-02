/**
 * PaymentListPage — lists all payments with optional customer filter.
 *
 * This replaces the PlaceholderPage at /office/payments.
 * Payments are fetched via GET /payments (list endpoint).
 */
import { useState } from "react";
import { Link } from "react-router";
import { usePaymentsList } from "@/api/hooks/usePayments";
import { ROUTES } from "@/lib/constants";

const METHOD_LABELS: Record<string, string> = {
  CASH: "Cash",
  BANK_TRANSFER: "Bank Transfer",
  CHEQUE: "Cheque",
  CARD: "Card",
  MOBILE_WALLET: "Mobile Wallet",
};

const PAGE_SIZE = 25;

export function PaymentListPage() {
  const [page, setPage] = useState(0);

  const { data: payments, isLoading } = usePaymentsList({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">Payments</h1>

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : (payments ?? []).length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6">
          <p className="text-sm text-gray-500">No payments recorded yet.</p>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Payment #
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    Amount
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Method
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Reference
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                    Unallocated
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Received
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Allocations
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {payments!.map((payment: any) => (
                  <tr key={payment.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <Link
                        to={`${ROUTES.OFFICE}/payments/${payment.id}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {payment.payment_number}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                      {Number(payment.amount).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                      {METHOD_LABELS[payment.method] ?? payment.method}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {payment.reference ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                      <span
                        className={
                          Number(payment.unallocated_amount) > 0
                            ? "font-medium text-amber-600"
                            : "text-gray-500"
                        }
                      >
                        {Number(payment.unallocated_amount).toLocaleString()}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {new Date(payment.received_at).toLocaleDateString()}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {(payment.allocations ?? []).length > 0
                        ? `${payment.allocations.length} invoice(s)`
                        : "None"}
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
              disabled={(payments ?? []).length < PAGE_SIZE}
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
