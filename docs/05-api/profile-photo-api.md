# TourCRM — Profile Photo API

**Status:** APPROVED / CANONICAL for TH-0119  
**Scope:** Person profile photo transport, storage lifecycle and authorization

## 1. Purpose

This contract defines the API for the current profile photo associated with a Person.

The photo is a mutable profile attribute represented by `Person.photo_file_id`. It is not a versioned Document history.

## 2. Endpoints

All routes are under `/api/v1`.

### GET /persons/{person_id}/photo

Returns the current profile photo.

Authorization uses the existing Person read/object-relationship policy.

The endpoint must not expose:

- `storage_key`;
- storage backend internals;
- filesystem paths;
- public object-storage URLs.

If the Person has no current photo, use the existing API not-found/current-resource error conventions; do not invent a parallel error architecture.

### PUT /persons/{person_id}/photo

Replaces or creates the current profile photo.

Request:

`multipart/form-data`

Field:

`photo=<binary>`

Accepted source formats:

- JPEG;
- PNG;
- WebP.

Maximum source upload size:

- 10 MB.

The backend must validate actual image content, normalize EXIF orientation and produce the canonical stored representation:

- 512 × 512 px;
- WebP;
- square;
- processed crop only.

The backend is authoritative even though the frontend provides the crop UX.

Authorization uses the existing Person update/self rules. No new avatar-specific permission is introduced.

### DELETE /persons/{person_id}/photo

Removes the current profile photo.

The operation clears `Person.photo_file_id` and removes the current storage object.

Repeated deletion when no current photo exists is safe according to existing API conventions.

## 3. /auth/me projection

`GET /api/v1/auth/me` must expose the following Person fields required by the authenticated UI:

- `id`;
- `first_name`;
- `last_name`;
- `middle_name`;
- `birth_date`;
- `photo_file_id`.

No storage internals are exposed.

## 4. Storage

Use the existing `FileStorage` port.

The port already provides:

- `put()`;
- `get()`;
- `exists()`;
- `delete()`.

Do not create a second storage abstraction.

Storage objects are private.

## 5. Replacement transaction

The canonical replacement sequence is:

1. validate/process the new image;
2. store a new immutable storage object;
3. create the corresponding File record;
4. update `Person.photo_file_id`;
5. commit the database transaction;
6. delete the previous storage object after successful commit.

If the database transaction fails after the new object is stored, the new object is deleted as compensation and the old photo remains current.

If deletion of the old object fails after the database commit, the new photo remains current. The DB state is not rolled back.

No avatar history is retained.

## 6. File lifecycle

The original uploaded image is not retained.

Only the processed 512×512 WebP crop is stored.

A previous avatar file must not remain as an intentionally retained historical avatar.

Orphan-file cleanup remains covered by the general file-retention/storage policy.

## 7. Security

- actual image content must be validated;
- client MIME type/extension alone is insufficient;
- protect against oversized/decompression-bomb images;
- no public storage access;
- no `storage_key` in API responses;
- no image bytes in application logs;
- existing Person authorization remains authoritative.

## 8. Cache invalidation

Photo responses require a cache/versioning strategy so a replacement cannot indefinitely serve the previous image.

The frontend may use the current `photo_file_id` as a server-authoritative version identifier when constructing the photo URL.

## 9. Testing contract

Tests must cover:

- JPEG/PNG/WebP;
- invalid image content;
- upload size limit;
- EXIF orientation;
- canonical 512×512 WebP output;
- authorization;
- successful replacement;
- failed replacement preserving the old photo;
- old object deletion after successful replacement;
- delete;
- repeated delete;
- no storage_key leakage.
