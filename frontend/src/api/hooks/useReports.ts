import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

// ---------------------------------------------------------------------------
// List report types (read-only, no permission gate)
// ---------------------------------------------------------------------------

/** Fetch the seeded report type catalog (id, code). */
export function useReportTypes() {
  return useQuery({
    queryKey: ["report-types"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/report-types" as any,
        { headers: authHeader() } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items as Array<{ id: string; code: string }>;
    },
  });
}

// ---------------------------------------------------------------------------
// List report definitions
// ---------------------------------------------------------------------------

interface ListDefinitionsParams {
  skip?: number;
  limit?: number;
}

/** Fetch report definitions (REPORT_MANAGE required). */
export function useReportDefinitions(params: ListDefinitionsParams = {}) {
  const { skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["report-definitions", { skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/report-definitions" as any,
        {
          params: { query: { skip, limit } },
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items as Array<{
        id: string;
        report_type_id: string;
        owner_user_id: string;
        name: string;
        parameters: Record<string, unknown>;
        output_format: string;
        schedule_cron: string | null;
        is_active: boolean;
        created_at: string;
      }>;
    },
  });
}

// ---------------------------------------------------------------------------
// Create report definition
// ---------------------------------------------------------------------------

/** Create a new report definition. */
export function useCreateReportDefinition() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      report_type_id: string;
      name: string;
      parameters?: Record<string, unknown>;
      output_format?: string;
      schedule_cron?: string | null;
    }) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/report-definitions",
        {
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["report-definitions"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Run a report (synchronous)
// ---------------------------------------------------------------------------

/** Execute a report synchronously — returns run + snapshot inline. */
export function useRunReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (reportDefinitionId: string) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/report-definitions/{report_definition_id}/run",
        {
          params: { path: { report_definition_id: reportDefinitionId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data as {
        run: {
          id: string;
          status: string;
          started_at: string | null;
          completed_at: string | null;
          row_count: number | null;
        };
        snapshot: {
          id: string;
          snapshot_data: Record<string, unknown>;
          row_count: number;
          captured_at: string;
        } | null;
      };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["report-definitions"] });
      queryClient.invalidateQueries({ queryKey: ["report-runs"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Get a report run
// ---------------------------------------------------------------------------

/** Fetch a past report run with its snapshot. */
export function useReportRun(runId: string | null) {
  return useQuery({
    queryKey: ["report-runs", runId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/api/v1/report-runs/{report_run_id}" as any,
        {
          params: { path: { report_run_id: runId! } },
          headers: authHeader(),
        } as any,
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data as {
        run: {
          id: string;
          status: string;
          started_at: string | null;
          completed_at: string | null;
          row_count: number | null;
          report_definition_id: string;
        };
        snapshot: {
          id: string;
          snapshot_data: Record<string, unknown>;
          row_count: number;
          captured_at: string;
        } | null;
      };
    },
    enabled: !!runId,
  });
}
