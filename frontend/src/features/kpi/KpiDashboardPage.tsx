import { useState } from "react";
import {
  useKpiLatest,
  useKpiHistory,
  useCaptureKpi,
  type KpiKey,
  type PeriodGranularity,
} from "@/api/hooks/useKpi";
import { usePermission } from "@/hooks/usePermission";
import { PERMISSIONS } from "@/lib/constants";

const KPI_CARDS: Array<{
  key: KpiKey;
  label: string;
  description: string;
  color: string;
}> = [
  {
    key: "TOTAL_STOCK_VALUE",
    label: "Total Stock Value",
    description: "Sum of inventory across all warehouses",
    color: "bg-blue-50 border-blue-200",
  },
  {
    key: "AR_BALANCE",
    label: "Accounts Receivable",
    description: "Outstanding customer balances",
    color: "bg-amber-50 border-amber-200",
  },
  {
    key: "COMMISSION_PAYABLE",
    label: "Commission Payable",
    description: "Unpaid representative commissions",
    color: "bg-purple-50 border-purple-200",
  },
];

export function KpiDashboardPage() {
  const canCapture = usePermission(PERMISSIONS.KPI_SNAPSHOT_VIEW); // VIEW implies capture ability for now
  const captureKpi = useCaptureKpi();
  const [selectedKey, setSelectedKey] = useState<KpiKey | null>(null);
  const [granularity, setGranularity] =
    useState<PeriodGranularity>("MONTHLY");

  async function handleCapture() {
    try {
      await captureKpi.mutateAsync(granularity);
    } catch {
      // Error surfaces via React Query
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">KPI Dashboard</h1>
          <p className="mt-1 text-sm text-gray-500">
            Global key performance indicators, computed from live ledger data.
          </p>
        </div>
        {canCapture && (
          <button
            onClick={handleCapture}
            disabled={captureKpi.isPending}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {captureKpi.isPending ? "Capturing…" : "Capture KPIs"}
          </button>
        )}
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {KPI_CARDS.map((card) => (
          <KpiCard
            key={card.key}
            kpiKey={card.key}
            label={card.label}
            description={card.description}
            color={card.color}
            isSelected={selectedKey === card.key}
            onClick={() =>
              setSelectedKey(selectedKey === card.key ? null : card.key)
            }
          />
        ))}
      </div>

      {/* Capture result feedback */}
      {captureKpi.isSuccess && captureKpi.data && (
        <div className="mt-4 rounded-md bg-green-50 p-3 text-sm text-green-700">
          KPIs captured successfully at{" "}
          {new Date(captureKpi.data.captured_at).toLocaleString()}.
        </div>
      )}
      {captureKpi.isError && (
        <div className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
          Failed to capture KPIs.
        </div>
      )}

      {/* History panel */}
      {selectedKey && (
        <KpiHistoryPanel
          kpiKey={selectedKey}
          granularity={granularity}
          onGranularityChange={setGranularity}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// KPI Card
// ---------------------------------------------------------------------------

function KpiCard({
  kpiKey,
  label,
  description,
  color,
  isSelected,
  onClick,
}: {
  kpiKey: KpiKey;
  label: string;
  description: string;
  color: string;
  isSelected: boolean;
  onClick: () => void;
}) {
  const { data: latest, isLoading } = useKpiLatest(kpiKey);

  return (
    <button
      onClick={onClick}
      className={`rounded-lg border-2 p-6 text-left transition-all hover:shadow-md ${
        isSelected ? "ring-2 ring-blue-500 " : ""
      } ${color}`}
    >
      <h3 className="text-sm font-medium text-gray-500">{label}</h3>
      {isLoading ? (
        <p className="mt-2 text-2xl font-bold text-gray-400">…</p>
      ) : latest ? (
        <p className="mt-2 text-2xl font-bold text-gray-900">
          {Number(latest.value).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          })}
        </p>
      ) : (
        <p className="mt-2 text-2xl font-bold text-gray-400">No data</p>
      )}
      <p className="mt-1 text-xs text-gray-500">{description}</p>
      {latest && (
        <p className="mt-2 text-xs text-gray-400">
          Last captured: {new Date(latest.captured_at).toLocaleDateString()}
        </p>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// KPI History Panel
// ---------------------------------------------------------------------------

function KpiHistoryPanel({
  kpiKey,
  granularity,
  onGranularityChange,
}: {
  kpiKey: KpiKey;
  granularity: PeriodGranularity;
  onGranularityChange: (g: PeriodGranularity) => void;
}) {
  const { data: history, isLoading } = useKpiHistory({
    kpiKey,
    periodGranularity: granularity,
    limit: 20,
  });

  return (
    <div className="mt-6 rounded-lg border border-gray-200 bg-white p-6">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">
          {kpiKey.replace(/_/g, " ")} — History
        </h2>
        <select
          value={granularity}
          onChange={(e) =>
            onGranularityChange(e.target.value as PeriodGranularity)
          }
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm"
        >
          <option value="DAILY">Daily</option>
          <option value="WEEKLY">Weekly</option>
          <option value="MONTHLY">Monthly</option>
        </select>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}

      {!isLoading && (history ?? []).length === 0 && (
        <p className="text-sm text-gray-500">
          No history data. Capture KPIs to see trend data.
        </p>
      )}

      {!isLoading && (history ?? []).length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs font-medium uppercase text-gray-500">
                <th className="pb-2">Date</th>
                <th className="pb-2">Granularity</th>
                <th className="pb-2 text-right">Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {(history ?? []).map((snap) => (
                <tr key={snap.id} className="hover:bg-gray-50">
                  <td className="py-2 text-sm text-gray-900">
                    {new Date(snap.captured_at).toLocaleString()}
                  </td>
                  <td className="py-2 text-sm text-gray-500">
                    {snap.period_granularity}
                  </td>
                  <td className="py-2 text-right text-sm font-medium text-gray-900">
                    {Number(snap.value).toLocaleString(undefined, {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
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
