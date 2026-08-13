# FlowCanvas gateway contract

`POST /v1/gateway/generations` is a synchronous server-to-server execution boundary. Required headers are `Authorization: Bearer ...` and a stable `Idempotency-Key` of at most 255 characters.

```json
{
  "kind": "image",
  "prompt": "A premium product shot",
  "storage_mode": "caller_owned",
  "inputs": [{
    "asset_key": "references/product.png",
    "checksum_sha256": "<64 hex characters>",
    "mime_type": "image/png",
    "size_bytes": 12345,
    "download_url": "https://storage.flowcanvas.example/signed-input"
  }],
  "output_destinations": [{
    "output_index": 0,
    "upload_url": "https://storage.flowcanvas.example/signed-output"
  }],
  "options": {"aspect_ratio": "9:16"}
}
```

`kind` supports `image`, `video`, and `omni`. Image supports one to four destinations. Image-to-video requires exactly one input and one destination. Omni requires one to eight image inputs and exactly one destination.

Successful responses have `status: "done"` and include only `output_index`, type, MIME, size, SHA-256 and `uploaded`. FlowCanvas must verify the stored object and create its final Asset record.

FlowCanvas owns the durable idempotency lock and must not issue concurrent calls for the same logical key. Reusing the key gives the same deterministic gateway task ID, but it does not suppress provider execution inside this stateless process. A lost response is therefore an uncertain execution that FlowCanvas must reconcile before retrying.
