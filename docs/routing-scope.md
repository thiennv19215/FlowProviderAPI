# Provider routing scope

`X-Provider-Routing-Scope` is a compatibility mechanism for requests that must stay on the same signed-in Google Flow account. Most managed generation calls should omit `project_id`, send image bytes through `input_images`, and let FlowProviderAPI choose/recover an eligible account/project automatically.

## Authentication is separate

Production `/v1/*` calls always require:

```http
Authorization: Bearer <FLOW_PROVIDER_BOOTSTRAP_API_KEY>
```

The routing scope is **not** an authentication token. It cannot replace the business Bearer key.

## When a scope is useful

Google Flow project/media IDs are account-scoped. A caller using explicit Google project/media identity may need to keep later operations on the same connector/account.

Current endpoints that can consume/return routing context include media and generation calls such as:

```http
POST /v1/media
POST /v1/images/generations
POST /v1/videos/generations
```

There is no public `/v1/projects` endpoint in the current API. Managed project lookup/creation happens inside FlowProviderAPI.

A response can include:

```http
X-Provider-Routing-Scope: <opaque-token>
X-Flow-Project-Id: <google-project-id>
```

If the integrating application intentionally continues an explicit project/account workflow, persist the opaque scope together with its related project/media context and resend it on compatible follow-up requests.

## Managed workflow recommendation

For new server-to-server integrations:

1. omit `project_id`;
2. pass caller-owned reference bytes via `input_images`;
3. persist Provider `jobs[].id` rather than Google operation identity;
4. poll `/v1/jobs/status`;
5. let the worker handle account selection, credit filtering and safe media rehydration.

This minimizes sticky account coupling and makes failover safer.

## Failure behavior

Malformed/tampered scope:

```text
400 ROUTING_SCOPE_INVALID
```

Bound route currently unavailable:

```text
503 ROUTING_SCOPE_UNAVAILABLE
```

Known project/media ownership conflicts can return 409 errors such as `PROJECT_ACCOUNT_MISMATCH`, `MEDIA_ACCOUNT_MISMATCH`, `MEDIA_PROJECT_MISMATCH`, or `PROJECT_ROUTE_UNKNOWN` depending on the request.

A truly scoped workflow must not silently send account-bound Google resources through an unrelated account. For unscoped managed work with durable source images, Provider can choose a different eligible account and rehydrate the image into that account's managed project.

## Security and lifecycle

The current v2 scope is HMAC-signed using the private extension connector credential (`FLOW_PROVIDER_EXTENSION_API_KEY` in production) and encodes the extension installation identity plus normalized Google account email. It contains no Google cookie/token.

Rotating the extension connector key invalidates previously issued routing-scope signatures. Changing the Google account signed into an installation also makes an old account-bound route unusable.

The business API key and routing-scope signing key intentionally have different responsibilities:

- business key: authenticate trusted `/v1/*` callers;
- extension key: authenticate private connectors and sign account-routing scopes.

## Durable source assets

Inline generation inputs used by queued jobs are persisted content-addressed before enqueueing, so restart recovery does not rely solely on in-memory bytes. Character references are also retained in the durable asset store. Account/project-specific Google media IDs remain cached in SQLite and can be recreated when source bytes are available.
