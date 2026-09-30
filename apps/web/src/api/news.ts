import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError, type CollectionResponse } from "./client";

/**
 * News / Announcements (TH-0120 / Issue #227; docs/04-ux/news.md).
 *
 * Visibility is decided entirely by the backend (`GET /api/v1/news` only
 * ever returns what the signed-in user may see — audience, lifecycle and
 * role are resolved server-side). This module never filters, sorts or
 * otherwise derives visibility from `audience_type`/`group_ids`; those
 * fields are only transported for the Administrator's edit form.
 */

export type NewsStatus = "draft" | "published" | "archived";
export type NewsAudienceType = "club" | "groups";

export type NewsLinkedEvent = {
  id: string;
  title: string;
  start_at: string;
  end_at: string;
  status: string;
};

export type News = {
  id: string;
  title: string;
  body: string;
  status: NewsStatus;
  published_at: string | null;
  archived_at: string | null;
  event_date: string | null;
  location: string | null;
  audience_type: NewsAudienceType;
  /** Administrator only; `null` for every other reader. */
  group_ids: string[] | null;
  image_file_id: string | null;
  linked_event: NewsLinkedEvent | null;
  created_by: string;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
};

/** `published` is the ordinary list (Home, «Все новости»); the other
 * values are the Administrator's management views. */
export type NewsListStatus = NewsStatus | "all";

export const NEWS_HOME_LIMIT = 5;

export function useNewsList(params: { status?: NewsListStatus; page?: number; pageSize?: number } = {}) {
  const status = params.status ?? "published";
  const page = params.page ?? 1;
  const pageSize = params.pageSize ?? 20;
  return useQuery<CollectionResponse<News>, ApiError>({
    queryKey: ["news", "list", status, page, pageSize],
    queryFn: () =>
      apiFetch<CollectionResponse<News>>(
        `/news?status=${status}&page=${page}&page_size=${pageSize}`,
      ),
  });
}

export function useNews(newsId: string | undefined) {
  return useQuery<News, ApiError>({
    queryKey: ["news", "detail", newsId],
    queryFn: () => apiFetch<News>(`/news/${encodeURIComponent(newsId ?? "")}`),
    enabled: Boolean(newsId),
  });
}

/** Image URL versioned by the server-authoritative `image_file_id` (same
 * scheme as the profile photo), `null` when the News has no image. */
export function newsImageUrl(news: Pick<News, "id" | "image_file_id">): string | null {
  if (!news.image_file_id) return null;
  return `/api/v1/news/${encodeURIComponent(news.id)}/image?v=${encodeURIComponent(news.image_file_id)}`;
}

export type NewsFields = {
  title: string;
  body: string;
  audience_type: NewsAudienceType;
  group_ids: string[];
  event_date: string | null;
  location: string | null;
  event_id: string | null;
};

export type NewsCreateFields = NewsFields & { status: "draft" | "published" };

function useInvalidateNews() {
  const queryClient = useQueryClient();
  return (news?: News) => {
    if (news) queryClient.setQueryData(["news", "detail", news.id], news);
    void queryClient.invalidateQueries({ queryKey: ["news"] });
  };
}

export function useCreateNews() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, NewsCreateFields>({
    mutationFn: (fields) => apiFetch<News>("/news", { method: "POST", body: JSON.stringify(fields) }),
    onSuccess: (news) => invalidate(news),
  });
}

export function useUpdateNews() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, { newsId: string; fields: Partial<NewsFields> }>({
    mutationFn: ({ newsId, fields }) =>
      apiFetch<News>(`/news/${encodeURIComponent(newsId)}`, {
        method: "PATCH",
        body: JSON.stringify(fields),
      }),
    onSuccess: (news) => invalidate(news),
  });
}

export function usePublishNews() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, string>({
    mutationFn: (newsId) =>
      apiFetch<News>(`/news/${encodeURIComponent(newsId)}/publish`, { method: "POST" }),
    onSuccess: (news) => invalidate(news),
  });
}

export function useArchiveNews() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, string>({
    mutationFn: (newsId) =>
      apiFetch<News>(`/news/${encodeURIComponent(newsId)}/archive`, { method: "POST" }),
    onSuccess: (news) => invalidate(news),
  });
}

export function useUploadNewsImage() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, { newsId: string; image: File }>({
    mutationFn: ({ newsId, image }) => {
      const body = new FormData();
      body.append("image", image, image.name);
      return apiFetch<News>(`/news/${encodeURIComponent(newsId)}/image`, { method: "PUT", body });
    },
    onSuccess: (news) => invalidate(news),
  });
}

export function useDeleteNewsImage() {
  const invalidate = useInvalidateNews();
  return useMutation<News, ApiError, string>({
    mutationFn: (newsId) =>
      apiFetch<News>(`/news/${encodeURIComponent(newsId)}/image`, { method: "DELETE" }),
    onSuccess: (news) => invalidate(news),
  });
}
