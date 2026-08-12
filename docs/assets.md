# Assets

Provider outputs are represented by compact `asset_*` IDs and returned with the direct image or video URL supplied by Google Flow. The output bytes are not copied into Provider-owned storage. Google Flow media IDs remain internal and are never part of the public contract.

For user-supplied reference media, call `POST /v1/assets/uploads`, upload with the returned descriptor, then call `POST /v1/assets/{id}/complete`. The authenticated PUT writes to local storage; in production that path is backed by a durable Docker volume.

The Google Flow adapter transparently maps `(asset_id, provider_project_id)` to the project-local Flow media ID. This lets a generated output be passed later in `reference_asset_ids` without downloading and uploading it again when the same Flow project is reused.

Direct Flow URLs are upstream-owned and may expire or be revoked. Consumers that need permanent media should download each successful output promptly and store it in their own durable storage.
