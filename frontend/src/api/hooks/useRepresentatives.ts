import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, authHeader } from "@/api/client";
import { extractErrorMessage } from "@/utils/extractErrorMessage";

/** Delete a representative by ID. */
export function useDeleteRepresentative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (representativeId: string) => {
      const { error } = await apiClient.DELETE(
        "/api/v1/representatives/{representative_id}",
        {
          params: { path: { representative_id: representativeId } },
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["representatives"] });
    },
  });
}

interface ListRepresentativesParams {
  skip?: number;
  limit?: number;
  search?: string;
  status?: string;
}

/** Fetch representatives with pagination and optional search/status filter. */
export function useRepresentatives(params: ListRepresentativesParams = {}) {
  const { skip = 0, limit = 50, search, status } = params;
  return useQuery({
    queryKey: ["representatives", { skip, limit, search, status }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/v1/representatives", {
        params: {
          query: {
            skip,
            limit,
            ...(search ? { search } : {}),
            ...(status ? { status: status as any } : {}),
          },
        },
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data.items;
    },
  });
}

/** Update a representative by ID. */
export function useUpdateRepresentative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      representativeId,
      ...body
    }: {
      representativeId: string;
      person_name?: string;
      national_id?: string;
      tax_id?: string;
      home_city_ref_id?: string;
      status?: string;
    }) => {
      const { data, error } = await apiClient.PATCH(
        "/api/v1/representatives/{representative_id}",
        {
          params: { path: { representative_id: representativeId } },
          body: body as any,
          headers: authHeader(),
        },
      );
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["representatives"] });
    },
  });
}

/** Create a new representative. */
export function useCreateRepresentative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      code: string;
      person_name: string;
      national_id?: string;
      tax_id?: string;
      home_city_ref_id?: string;
      phone_number?: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/v1/representatives", {
        body: body as any,
        headers: authHeader(),
      });
      if (error) throw new Error(extractErrorMessage(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["representatives"] });
    },
  });
}
