import { useParams, Link } from "react-router";
import { usePayment } from "@/api/hooks/usePayments";
import { ROUTES } from "@/lib/constants";

export function PaymentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: payment, isLoading, error } = usePayment(id ?? "");

  if (isLoading) {
    return <p className="text-gray-500">Loading…</p>;
  }

  if (error || !payment) {
    return (
      <div>
        <p className="text-red-600">Payment not found.</p>
        <Link
          to={ROUTES.OFFICE}
          className="mt-4 inline-block text-sm text-blue-600 hover:underline"
        >
          ← Back to office
        </Link>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <Link
          to={ROUTES.OFFICE}
          className="text-sm text-blue-600 hover:underline"
        >
          ← Office
        </Link>
      </div>

      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Payment {payment.payment_number}
      </h1>

      {/* Payment details */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-gray-500">Amount</dt>
            <dd className="mt-1 text-sm font-bold text-gray-900">
              {Number(payment.amount).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Method</dt>
            <dd className="mt-1 text-sm text-gray-900">{payment.method}</dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Reference</dt>
            <dd className="mt-1 text-sm text-gray-900">
              {payment.reference ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Customer</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {payment.customer_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Currency</dt>
            <dd className="mt-1 font-mono text-xs text-gray-900">
              {payment.currency_id}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">
              Unallocated Amount
            </dt>
            <dd className="mt-1 text-sm text-gray-900">
              {Number(payment.unallocated_amount).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Received At</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(payment.received_at).toLocaleString()}
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-gray-500">Created</dt>
            <dd className="mt-1 text-sm text-gray-600">
              {new Date(payment.created_at).toLocaleString()}
            </dd>
          </div>
        </dl>
      </div>

      {/* Allocations */}
      <div>
        <h2 className="mb-3 text-lg font-semibold text-gray-900">
          Allocations
        </h2>
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Invoice
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-gray-500">
                  Allocated Amount
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Allocated At
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {payment.allocations.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
                    className="px-4 py-8 text-center text-sm text-gray-500"
                  >
                    No allocations.
                  </td>
                </tr>
              ) : (
                payment.allocations.map((alloc) => (
                  <tr key={alloc.id} className="hover:bg-gray-50">
                    <td className="whitespace-nowrap px-4 py-3 text-sm">
                      <Link
                        to={`${ROUTES.OFFICE}/invoices/${alloc.invoice_id}`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        <span className="font-mono text-xs">
                          {alloc.invoice_id.slice(0, 8)}
                        </span>
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-sm font-medium text-gray-900">
                      {Number(alloc.allocated_amount).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                      {new Date(alloc.allocated_at).toLocaleString()}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
