# Media

Provider outputs are represented by compact string IDs and returned with the direct image or video URL supplied by Google Flow. The output bytes are not copied into Provider-owned storage. Google Flow media IDs remain internal and are never part of the public contract.

For user-supplied reference media, call `POST /v1/media` with the file as multipart field `file`. The API validates and stores it before returning a ready media object. In production, user uploads are backed by a durable Docker volume.

`media_id` is an opaque media reference. Generation accepts `reference_media_ids` and `start_media_id`.

The Google Flow adapter transparently maps `(media_id, provider_project_id)` to the project-local Flow media ID. This lets a generated output be passed later in `reference_media_ids` without downloading and uploading it again when the same Flow project is reused.

Direct Flow URLs are upstream-owned and may expire or be revoked. Consumers that need permanent media should download each successful output promptly and store it in their own durable storage.
