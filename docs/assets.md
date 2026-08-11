# Assets

Provider outputs are copied into Provider-owned storage and represented by `asset_*` IDs. Google Flow media IDs are never part of the public contract.

For reference media, call `POST /v1/assets/uploads`, upload with the returned descriptor, then call `POST /v1/assets/{id}/complete`. Local development accepts authenticated PUTs to `/v1/assets/{id}/content`; R2 deployments return presigned PUT URLs.

A Google Flow adapter transparently maps `(asset_id, provider_project_id)` to the project-local Flow media ID.
