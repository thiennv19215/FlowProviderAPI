# Assets

In production gateway-only mode, FlowProviderAPI owns no Asset rows or durable media objects. FlowCanvas supplies narrowly scoped signed HTTPS download/upload URLs on exact allowlisted hosts.

The gateway bounds downloads, validates an optional reference SHA-256, uploads references into the temporary Google project, validates Google output hosts and redirects, then PUTs output bytes directly to FlowCanvas storage. Signed URLs and Google URLs are never returned in the result.

The legacy `/v1/media` API and compact Provider media IDs have been removed.
