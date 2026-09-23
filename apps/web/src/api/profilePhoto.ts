import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiFetch, ApiError } from "./client";
import type { MeResponse } from "./auth";
import type { Person } from "./people";

/**
 * Person profile photo (TH-0119 / Issue #176; docs/05-api/
 * profile-photo-api.md). The backend validates and normalizes the image
 * and owns the storage lifecycle — this module only transports the
 * user's crop and keeps the authenticated-user query in sync.
 */

/** URL of the current photo, versioned by the server-authoritative
 * `photo_file_id` so a replacement is never served from a stale cache.
 * `null` when the person has no photo (initials fallback). */
export function personPhotoUrl(personId: string, photoFileId: string | null): string | null {
  if (!photoFileId) return null;
  return `/api/v1/persons/${encodeURIComponent(personId)}/photo?v=${encodeURIComponent(photoFileId)}`;
}

export function currentUserPhotoUrl(me: MeResponse | undefined): string | undefined {
  const person = me?.user.person;
  if (!person) return undefined;
  return personPhotoUrl(person.id, person.photo_file_id) ?? undefined;
}

function setCachedPhotoFileId(
  queryClient: ReturnType<typeof useQueryClient>,
  personId: string,
  photoFileId: string | null,
) {
  queryClient.setQueryData<MeResponse>(["auth", "me"], (me) =>
    me && me.user.person.id === personId
      ? { ...me, user: { ...me.user, person: { ...me.user.person, photo_file_id: photoFileId } } }
      : me,
  );
  void queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
  void queryClient.invalidateQueries({ queryKey: ["persons", "detail", personId] });
}

/** `PUT /api/v1/persons/{person_id}/photo` (multipart `photo`). */
export function useUploadPersonPhoto() {
  const queryClient = useQueryClient();
  return useMutation<Person, ApiError, { personId: string; photo: Blob }>({
    mutationFn: ({ personId, photo }) => {
      const body = new FormData();
      body.append("photo", photo, "profile-photo.webp");
      return apiFetch<Person>(`/persons/${encodeURIComponent(personId)}/photo`, {
        method: "PUT",
        body,
      });
    },
    onSuccess: (person, { personId }) => {
      setCachedPhotoFileId(queryClient, personId, person.photo_file_id);
    },
  });
}

/** `DELETE /api/v1/persons/{person_id}/photo` — safe to repeat. */
export function useDeletePersonPhoto() {
  const queryClient = useQueryClient();
  return useMutation<void, ApiError, { personId: string }>({
    mutationFn: ({ personId }) =>
      apiFetch<void>(`/persons/${encodeURIComponent(personId)}/photo`, { method: "DELETE" }),
    onSuccess: (_result, { personId }) => {
      setCachedPhotoFileId(queryClient, personId, null);
    },
  });
}
