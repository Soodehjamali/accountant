import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";

export type KpiKey = "TOTAL_STOCK_VALUE" | "AR_BALANCE" | "COMMISSION_PAYABLE";
export type PeriodGranularity = "DAILY" | "WEEKLY" | "MONTHLY";

interface KpiSnapshot {
  id: string;
  kpi_key: string;
  scope_type: string;
  scope_id: string | null;
  value: string;
  captured_at: string;
  period_granularity: string;
}

// ---------------------------------------------------------------------------
// Get latest KPI value
// ---------------------------------------------------------------------------

/** Fetch the most recently captured value for a KPI key. */
export function useKpiLatest(kpiKey: KpiKey, scopeType = "GLOBAL") {
  return useQuery({
    queryKey: ["kpi-latest", kpiKey, scopeType],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/kpi-snapshots/{kpi_key}/latest" as any,
        {
          params: {
            path: { kpi_key: kpiKey },
            query: { scope_type: scopeType },
          },
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(String(error));
      return (data ?? null) as KpiSnapshot | null;
    },
  });
}

// ---------------------------------------------------------------------------
// Get KPI history
// ---------------------------------------------------------------------------

interface KpiHistoryParams {
  kpiKey: KpiKey;
  scopeType?: string;
  periodGranularity?: PeriodGranularity;
  skip?: number;
  limit?: number;
}

/** Fetch trend-chart history for a KPI key (ordered by captured_at DESC). */
export function useKpiHistory(params: KpiHistoryParams) {
  const {
    kpiKey,
    scopeType = "GLOBAL",
    periodGranularity,
    skip = 0,
    limit = 50,
  } = params;
  return useQuery({
    queryKey: [
      "kpi-history",
      { kpiKey, scopeType, periodGranularity, skip, limit },
    ],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/kpi-snapshots/{kpi_key}/history" as any,
        {
          params: {
            path: { kpi_key: kpiKey },
            query: {
              scope_type: scopeType,
              ...(periodGranularity ? { period_granularity: periodGranularity } : {}),
              skip,
              limit,
            },
          },
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(String(error));
      return (data as any).items as KpiSnapshot[];
    },
  });
}

// ---------------------------------------------------------------------------
// Capture KPIs
// ---------------------------------------------------------------------------

/** Trigger on-demand KPI capture (GLOBAL scope). */
export function useCaptureKpi() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (periodGranularity: PeriodGranularity = "MONTHLY") => {
      const { data, error } = await apiClient.POST(
        "/api/v1/kpi-snapshots/capture",
        {
          body: { period_granularity: periodGranularity },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(String(error));
      return data as {
        items: KpiSnapshot[];
        captured_at: string;
      };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["kpi-latest"] });
      queryClient.invalidateQueries({ queryKey: ["kpi-history"] });
    },
  });
}
