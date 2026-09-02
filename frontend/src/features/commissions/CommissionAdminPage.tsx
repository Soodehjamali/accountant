import { useState } from "react";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";
import {
  useCommissionConfigs,
  useCommissionTransactions,
  useCreateCommissionConfig,
  useApproveCommission,
  usePayCommission,
  useClawbackCommission,
} from "@/api/hooks/useCommissions";
import { useRepresentatives } from "@/api/hooks/useRepresentatives";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATE_BADGE: Record<string, string> = {
  ACCRUED: "bg-yellow-100 text-yellow-800",
  APPROVED: "bg-blue-100 text-blue-800",
  PAID: "bg-green-100 text-green-800",
  CLAWED_BACK: "bg-red-100 text-red-800",
};

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function CommissionAdminPage() {
  const canManage = usePermission(PERMISSIONS.COMMISSION_MANAGE);

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold text-gray-900">
        Commission Management
      </h1>

      {/* Commission Configs Section */}
      <CommissionConfigsSection canManage={canManage} />

      {/* Commission Transactions Section */}
      <CommissionTransactionsSection canManage={canManage} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Commission Configs
// ---------------------------------------------------------------------------

function CommissionConfigsSection({ canManage }: { canManage: boolean }) {
  const { data: configs, isLoading } = useCommissionConfigs();
  const [showCreate, setShowCreate] = useState(false);

  return (
    <div className="mb-8">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          Commission Rate Configurations
        </h2>
        {canManage && (
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            {showCreate ? "Cancel" : "New Config"}
          </button>
        )}
      </div>

      {showCreate && canManage && (
        <div className="mb-6">
          <CreateConfigForm onCreated={() => setShowCreate(false)} />
        </div>
      )}

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : (configs ?? []).length === 0 ? (
        <p className="text-sm text-gray-500">
          No commission configurations yet. Create one to set commission rates.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Rate
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Order Type
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Representative
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Effective From
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Effective To
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {configs!.map((cfg) => (
                <tr key={cfg.id} className="hover:bg-gray-50">
                  <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">
                    {Number(cfg.rate)}%
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-900">
                    {cfg.order_type}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {cfg.representative_id
                      ? cfg.representative_id.slice(0, 8) + "…"
                      : "Global (all reps)"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {new Date(cfg.effective_from).toLocaleDateString()}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
                    {cfg.effective_to
                      ? new Date(cfg.effective_to).toLocaleDateString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create Config Form
// ---------------------------------------------------------------------------

function CreateConfigForm({ onCreated }: { onCreated: () => void }) {
  const createConfig = useCreateCommissionConfig();
  const { data: representatives } = useRepresentatives();
  const [rate, setRate] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");
  const [representativeId, setRepresentativeId] = useState("");
  const [orderType, setOrderType] = useState("LOCAL");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const rateNum = Number(rate);
    if (!rate || rateNum < 0 || rateNum > 100) {
      setError("Rate must be between 0 and 100.");
      return;
    }
    if (!effectiveFrom) {
      setError("Effective from date is required.");
      return;
    }

    try {
      await createConfig.mutateAsync({
        rate: rateNum,
        effective_from: new Date(effectiveFrom).toISOString(),
        effective_to: effectiveTo ? new Date(effectiveTo).toISOString() : null,
        representative_id: representativeId || null,
        order_type: orderType,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create config");
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-6">
      <h3 className="mb-4 text-sm font-semibold text-gray-900">
        Create Commission Config
      </h3>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Rate (%) *
            </label>
            <input
              type="number"
              step="0.01"
              min="0"
              max="100"
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              required
              placeholder="e.g. 5"
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Order Type *
            </label>
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="LOCAL">LOCAL</option>
              <option value="DIRECT">DIRECT</option>
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Representative (optional)
            </label>
            <select
              value={representativeId}
              onChange={(e) => setRepresentativeId(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            >
              <option value="">Global (all reps)</option>
              {(representatives ?? []).map((rep: any) => (
                <option key={rep.id} value={rep.id}>
                  {rep.person_name} ({rep.code})
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Effective From *
            </label>
            <input
              type="date"
              value={effectiveFrom}
              onChange={(e) => setEffectiveFrom(e.target.value)}
              required
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Effective To (optional)
            </label>
            <input
              type="date"
              value={effectiveTo}
              onChange={(e) => setEffectiveTo(e.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={createConfig.isPending}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {createConfig.isPending ? "Creating…" : "Create Config"}
        </button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Commission Transactions (with approval/payment actions)
// ---------------------------------------------------------------------------

function CommissionTransactionsSection({ canManage }: { canManage: boolean }) {
  const [page, setPage] = useState(0);
  const [stateFilter, setStateFilter] = useState("");
  const approveCommission = useApproveCommission();
  const payCommission = usePayCommission();
  const clawbackCommission = useClawbackCommission();

  const {
    data: transactions,
    isLoading,
  } = useCommissionTransactions({
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
    state_event: stateFilter || undefined,
  });

  return (
    <div>
      <h2 className="mb-3 text-lg font-semibold text-gray-900">
        Commission Transactions
      </h2>

      {/* State filter */}
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

      {isLoading ? (
        <p className="text-gray-500">Loading…</p>
      ) : (transactions ?? []).length === 0 ? (
        <p className="text-sm text-gray-500">No commission transactions yet.</p>
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
                    Representative
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Order
                  </th>
                  {canManage && (
                    <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                      Action
                    </th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {transactions!.map((txn) => (
                  <TransactionRow
                    key={txn.id}
                    txn={txn}
                    canManage={canManage}
                    approveCommission={approveCommission}
                    payCommission={payCommission}
                    clawbackCommission={clawbackCommission}
                  />
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
  );
}

// ---------------------------------------------------------------------------
// Transaction Row with Action Buttons
// ---------------------------------------------------------------------------

function TransactionRow({
  txn,
  canManage,
  approveCommission,
  payCommission,
  clawbackCommission,
}: {
  txn: any;
  canManage: boolean;
  approveCommission: any;
  payCommission: any;
  clawbackCommission: any;
}) {
  const [note, setNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleAction(
    action: "approve" | "pay" | "clawback",
  ) {
    setActionError(null);
    try {
      if (action === "approve") {
        await approveCommission.mutateAsync({
          transactionId: txn.id,
          note: note || null,
        });
      } else if (action === "pay") {
        await payCommission.mutateAsync({
          transactionId: txn.id,
          note: note || null,
        });
      } else {
        await clawbackCommission.mutateAsync({
          transactionId: txn.id,
          note: note || null,
        });
      }
      setNote("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    }
  }

  return (
    <tr className="hover:bg-gray-50">
      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">
        {new Date(txn.occurred_at).toLocaleDateString()}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm">
        <span
          className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
            STATE_BADGE[txn.state_event] ?? "bg-gray-100 text-gray-800"
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
        {txn.representative_id?.slice(0, 8) ?? "—"}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-500">
        {txn.order_id ? txn.order_id.slice(0, 8) + "…" : "—"}
      </td>
      {canManage && (
        <td className="whitespace-nowrap px-4 py-3 text-sm">
          {txn.state_event === "ACCRUED" && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="w-32 rounded border border-gray-300 px-2 py-1 text-xs"
              />
              <button
                onClick={() => handleAction("approve")}
                disabled={approveCommission.isPending}
                className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
              >
                Approve
              </button>
              <button
                onClick={() => handleAction("clawback")}
                disabled={clawbackCommission.isPending}
                className="rounded bg-red-100 px-2 py-1 text-xs text-red-700 hover:bg-red-200 disabled:opacity-50"
              >
                Clawback
              </button>
            </div>
          )}
          {txn.state_event === "APPROVED" && (
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="w-32 rounded border border-gray-300 px-2 py-1 text-xs"
              />
              <button
                onClick={() => handleAction("pay")}
                disabled={payCommission.isPending}
                className="rounded bg-green-600 px-2 py-1 text-xs text-white hover:bg-green-700 disabled:opacity-50"
              >
                Pay
              </button>
              <button
                onClick={() => handleAction("clawback")}
                disabled={clawbackCommission.isPending}
                className="rounded bg-red-100 px-2 py-1 text-xs text-red-700 hover:bg-red-200 disabled:opacity-50"
              >
                Clawback
              </button>
            </div>
          )}
          {actionError && (
            <span className="ml-2 text-xs text-red-600">{actionError}</span>
          )}
        </td>
      )}
    </tr>
  );
}
