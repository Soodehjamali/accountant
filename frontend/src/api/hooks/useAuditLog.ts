import { useQuery } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Fetch audit log entries with optional filters. */
export function useAuditLog(params: {
  entity_type?: string;
  entity_id?: string;
  skip?: number;
  limit?: number;
} = {}) {
  const { entity_type, entity_id, skip = 0, limit = 50 } = params;
  return useQuery({
    queryKey: ["audit-log", { entity_type, entity_id, skip, limit }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/audit-log" as any, {
        params: {
          query: {
            skip,
            limit,
            ...(entity_type ? { entity_type } : {}),
            ...(entity_id ? { entity_id: entity_id as any } : {}),
          },
        } as any,
        headers: authHeader(),
      } as any);
      if (error) throw new Error(extractErrorMessage(error));
      return (data as any).items;
    },
  });
}
