import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Fetch bot platform configs + live status. */
export function useBotConfigs() {
  return useQuery({
    queryKey: ["bot-configs"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/bot-config", {
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Save a platform config (enabled + optional token). */
export function useSaveBotConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      platform,
      enabled,
      token,
    }: {
      platform: string;
      enabled: boolean;
      token?: string;
    }) => {
      const { data, error } = await apiClient.PUT(
        "/api/v1/bot-config/{platform}",
        {
          params: { path: { platform } },
          body: { enabled, token: token ?? null } as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bot-configs"] });
    },
  });
}

/** Test the stored token against the platform API. */
export function useTestBotConnection() {
  return useMutation({
    mutationFn: async (platform: string) => {
      const { data, error } = await apiClient.POST(
        "/api/v1/bot-config/{platform}/test",
        { params: { path: { platform } }, headers: authHeader() },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
  });
}