# Media

Provider outputs are represented by compact string IDs. Image and video bytes are copied into Provider-owned storage, and the API returns its authenticated `/media/{media_id}` delivery URL. Google Flow media IDs remain internal and are never part of the public contract.

For user-supplied reference media, call `POST /v1/media` with the file as multipart field `file`. The API validates and stores it before returning a media object with `status: "done"`. In production, user uploads are stored in R2.

`media_id` is an opaque media reference. Generation accepts `reference_media_ids` and `start_media_id`.

The Google Flow adapter transparently maps `(media_id, provider_project_id)` to the project-local Flow media ID. This lets a generated output be passed later in `reference_media_ids` without downloading and uploading it again when the same Flow project is reused.

Direct Flow URLs are upstream-owned and may expire or be revoked. Consumers that need permanent media should download each successful output promptly and store it in their own durable storage.
