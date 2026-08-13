# Assets

FlowProviderAPI owns no Asset rows or durable media objects. `POST /v1/media` uploads a caller-supplied Base64 image directly into the selected Google Flow project, but the API does not persist the source image or generated output.

Any media URL in a Google Flow response passes back to the caller as part of the unmodified upstream body. The caller owns any later download, persistence and retention workflow.
