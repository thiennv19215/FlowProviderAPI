# Frontend integration guide

This guide is for a web or mobile frontend consuming FlowProvider through its **own application backend**.

> Do not put a FlowProvider Bearer API key in browser JavaScript, a mobile app bundle, or local storage. The frontend calls your backend; your backend calls FlowProvider with `Authorization: Bearer <API_KEY>`.

## Flow

```text
Browser UI -> Your backend -> FlowProvider
      ^                         |
      |---- task_id / result ----|
```

1. Upload optional reference media and keep its `media_id`.
2. Submit a generation request.
3. Store the returned `task_id`.
4. Poll `/v1/status/{task_id}` every 3–5 seconds while status is `queued` or `running`.
5. Render `outputs` on `succeeded`; show the nested `error` on `failed`.

Every generation POST is independent. V1 does not expose `Idempotency-Key`.

## TypeScript contract

```ts
type MediaId = string;

type TaskOutput = {
  media_id: MediaId;
  type: "image" | "video";
  url: string | null;
  thumbnail_url?: string | null;
};

type ProviderError = {
  status_code: number;
  code: string;
  message: string;
  details: Array<{ field: string | null; code: string; message: string }>;
  request_id: string | null;
  retryable: boolean;
};

type GenerationStatus = {
  task_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  outputs: TaskOutput[];
  error: ProviderError | null;
};
```

All public media IDs are opaque 15-digit JSON strings.

## Upload a reference

Your backend sends multipart form data to `POST /v1/media` with the file in field `file`. Keep the returned `media_id` for later generation requests.

Uploaded media content URLs are authenticated Provider endpoints, so browser media elements should normally receive a proxied or application-owned URL. Generated Flow output URLs are direct upstream URLs and may expire; persist important output promptly.

## Generate

Application backends should prefer `POST /v1/generations`:

```ts
const task = await providerFetch<GenerationStatus>("/v1/generations", {
  method: "POST",
  body: JSON.stringify({
    kind: "image",
    prompt: "A premium blue perfume bottle",
    media_ids: [uploadedMediaId],
    options: {
      model: "banana_pro",
      aspect_ratio: "9:16",
      output_count: 1,
    },
  }),
});
```

Compatibility endpoints remain available for image, image-to-video, and Omni generation.

## Poll status

```ts
async function waitForGeneration(taskId: string): Promise<GenerationStatus> {
  for (;;) {
    const result = await providerFetch<GenerationStatus>(`/v1/status/${taskId}`);
    if (result.status === "succeeded") return result;
    if (result.status === "failed" || result.status === "canceled") {
      throw new Error(result.error?.message ?? "Generation did not complete.");
    }
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
}
```

Do not create a new generation merely because an existing one is still queued. FlowProvider owns provider capacity and worker retry behavior.

## Cancellation

Your backend may call:

```http
POST /v1/status/{task_id}/cancel
```

Cancellation is cooperative and does not guarantee already-dispatched Google Flow work is stopped.

## Error handling

Synchronous request errors use the standard envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [],
    "request_id": "req_...",
    "retryable": false
  }
}
```

A known `/v1/status/{task_id}` lookup returns HTTP `200` even when the generation itself has `status: "failed"`; inspect the nested `error`.

## Useful endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/media` | Upload reference media |
| `GET /v1/media/{media_id}` | Read media metadata |
| `POST /v1/generations` | Preferred unified generation submission |
| `GET /v1/status/{task_id}` | Poll one generation |
| `GET /v1/status` | List caller-owned generation statuses |
| `POST /v1/status/{task_id}/cancel` | Request cooperative cancellation |
| `GET /v1/health` | Operational display only |
