# TourCRM — Profile Avatar Photo & Circular Crop Specification

**Status:** APPROVED / CANONICAL for TH-0119  
**Scope:** authenticated user's profile photo, Avatar component, Settings/profile UX  
**Owner:** Product Owner

## 1. Purpose

TourCRM keeps the existing round initials Avatar and adds the ability for a user to set, replace and delete their own profile photo.

This specification does not introduce a new avatar visual language.

## 2. Avatar states

The canonical state model is:

- `Person.photo_file_id = NULL` → existing initials Avatar.
- `Person.photo_file_id = UUID` → current profile photo.

There is no `avatar_type`, `avatar_code` or character/avatar catalog.

Default avatar characters are explicitly out of scope.

Avatar history is explicitly out of scope.

## 3. Existing Avatar visual language

The current Avatar remains:

- round;
- visually unchanged when initials are displayed;
- compatible with the existing ProfileMenu;
- compatible with existing Avatar sizes.

Adding photo support must not redesign the initials fallback.

No SVG and no generic icon-library artwork may be introduced for this feature.

## 4. Photo selection and crop

After selecting a photo, the user opens a crop editor.

The crop viewport is **round** because the product Avatar is round.

The editor provides:

- pan/drag;
- zoom in;
- zoom out;
- visible preview of the composition inside the round viewport;
- dimmed area outside the crop circle;
- Cancel;
- Save.

The user controls the composition.

The editor must not:

- detect faces;
- automatically center a face;
- automatically choose a crop;
- provide Photoshop-like editing.

## 5. Stored image

The crop viewport is round, but the stored image is **square**.

Canonical output:

- 512 × 512 px;
- WebP;
- RGB/standard web image output;
- no circular transparency/mask;
- only the processed crop is retained.

The original upload is not retained.

Backend is authoritative for final validation and normalization.

## 6. Input constraints

Accepted source formats:

- JPEG;
- PNG;
- WebP.

Initial maximum upload size:

- 10 MB.

The backend must validate actual image content, not only extension or client-provided MIME type.

The backend must normalize EXIF orientation and protect against oversized/decompression-bomb images.

## 7. Replacement lifecycle

A Person has only one current avatar photo.

Replacement order is mandatory:

1. validate/process new image;
2. store new object;
3. create new File record;
4. update Person.photo_file_id;
5. commit database transaction;
6. delete old storage object after successful commit.

The old photo must never be deleted before the new photo is safely persisted.

If persistence fails after the new object was stored, the new object must be removed as compensation and the old photo remains current.

If deletion of the old object fails after a successful DB commit, the new photo remains current. The application must not roll back the successful DB state.

No avatar history is retained.

## 8. Delete

Deleting the current photo:

- clears `Person.photo_file_id`;
- removes the current storage object;
- returns the UI to the existing initials Avatar.

Repeated delete is safe according to existing API conventions.

## 9. Settings UX

Profile photo management belongs in the existing Settings/profile flow.

Minimum controls:

- current Avatar;
- «Изменить фотографию»;
- «Удалить фотографию» when a photo exists.

After successful save or delete:

- update/invalidate the authenticated-user query;
- ProfileMenu must reflect the new state without a full page reload.

## 10. Person Detail

The temporary manual `photo_file_id` input is not a supported user-facing mechanism.

After TH-0119 implementation, Person Detail must not expose a text field for manually entering a photo file ID.

The persistence field `Person.photo_file_id` remains an internal domain reference.

## 11. Cache/versioning

Photo delivery must have a cache invalidation/versioning strategy.

A replacement must not leave the browser displaying the previous photo indefinitely.

A server-authoritative version identifier, such as the current `photo_file_id`, may be used in the photo URL/query.

## 12. Non-goals

This feature does not include:

- default avatar characters;
- avatar history;
- face recognition;
- automatic face positioning;
- public photo URLs;
- social/avatar galleries;
- photo filters;
- decorative avatar frames;
- SVG assets.

## 13. Acceptance

The feature is accepted only when:

- initials Avatar remains visually unchanged;
- crop viewport is round;
- pan and zoom work;
- saved output is square 512×512 WebP;
- original upload is not retained;
- replacement preserves the old photo if the new operation fails;
- successful replacement removes the old current storage object;
- delete returns to initials;
- ProfileMenu updates without reload;
- authorization is enforced by the existing Person model.
