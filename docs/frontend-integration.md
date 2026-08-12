# Frontend integration guide

This guide is for a web or mobile frontend consuming FlowProvider through its **own application backend**.

> Do not put a FlowProvider Bearer API key in browser JavaScript, a mobile app bundle, or local storage. Keep it on your backend. The frontend calls your backend; your backend adds `Authorization: Bearer <API_KEY>` when it calls FlowProvider.

Base URL: `https://api.shopcongngheso5.io.vn`

## Flow

```text
Browser UI -> Your backend -> FlowProvider
      ^                         |
      |---- task_id / result ----|
```

1. Upload an optional reference image and keep its `media_id`.
2. Submit an image, image-to-video, or Omni-video request.
3. Store the returned `task_id` in UI state.
4. Poll the task every 3–5 seconds while its status is `queued` or `running`.
5. Render `outputs` when the status becomes `succeeded`; show the nested `error` if it becomes `failed`.

Every submission creates a new task. Do not send a client-created task ID.

## TypeScript contract

All public media IDs are opaque 15-digit JSON strings. Keep them as strings across storage, URLs, and generation requests.

```ts
type MediaId = string;

type Media = {
  media_id: MediaId;
  object: "media";
  type: "image" | "video";
  status: "ready";
  mime_type: string;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  url: string | null;
  created_at: string;
};

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

type Task = {
  task_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  outputs: TaskOutput[];
  error: ProviderError | null;
};
```

Use `media_id`, never the old `id` field.

## Upload a reference image

Your backend sends `multipart/form-data` to `POST /v1/media` with the file in field `file`.

```ts
const form = new FormData();
form.set("file", file); // image/png, image/jpeg, or another image/* MIME type

const media = await providerFetch<Media>("/v1/media", {
  method: "POST",
  body: form,
});
// Save media.media_id and pass it in a later generation request.
```

Do not set a `Content-Type` header yourself when sending `FormData`; the HTTP client adds the multipart boundary.

For an uploaded file, `media.url` is an authenticated Provider endpoint. A browser `<img src={media.url}>` cannot attach the Bearer header. Preview it through your backend or fetch it with an authenticated request and create a blob URL:

```ts
const response = await providerFetchRaw(`/media/${media.media_id}`);
const previewUrl = URL.createObjectURL(await response.blob());
```

Generated Flow outputs instead return direct upstream URLs. They can normally be used directly in `<img>`, `<video>`, or a download link, but may expire; persist important output in your own storage promptly.

## Generate an image

`POST /v1/images/generations` returns HTTP `202` immediately.

```ts
const task = await providerFetch<Task>("/v1/images/generations", {
  method: "POST",
  body: JSON.stringify({
    prompt: "A blue perfume bottle, premium product photography",
    model: "banana_pro", // "banana_pro" | "banana_2"
    aspect_ratio: "9:16", // "1:1" | "16:9" | "9:16"
    output_count: 1, // 1–4
    reference_media_ids: [uploadedMediaId], // optional, maximum 8
  }),
});
```

Defaults: `model: "banana_pro"`, `aspect_ratio: "9:16"`, `output_count: 1`.

## Generate a video from one image

`POST /v1/videos/image-to-video` uses one image `start_media_id`.

```ts
const task = await providerFetch<Task>("/v1/videos/image-to-video", {
  method: "POST",
  body: JSON.stringify({
    prompt: "Slow vertical camera push-in with soft reflections",
    start_media_id: generatedImage.media_id,
    quality: "lite", // lite | fast | quality | lite_relaxed | fast_relaxed
    aspect_ratio: "9:16", // 16:9 | 9:16
  }),
});
```

Video only starts when a connected Google Flow account has sufficient credits. If none does, the task remains `queued` and reports a retryable `PROVIDER_ACCOUNT_UNAVAILABLE` error until capacity is available.

## Generate Omni video from multiple images

```ts
const task = await providerFetch<Task>("/v1/videos/omni-generations", {
  method: "POST",
  body: JSON.stringify({
    prompt: "The objects assemble into a cinematic vertical scene",
    reference_media_ids: [firstImageId, secondImageId],
    duration: 4, // 2 | 4 | 8 | 10 seconds
    aspect_ratio: "9:16", // 16:9 | 9:16
  }),
});
```

## Poll a task

Use a fixed 3–5 second interval. The API intentionally does not send `Retry-After` for task polling.

```ts
async function waitForTask(taskId: string): Promise<Task> {
  for (;;) {
    const task = await providerFetch<Task>(`/v1/tasks/${taskId}`);
    if (task.status === "succeeded") return task;
    if (task.status === "failed" || task.status === "canceled") {
      throw new Error(task.error?.message ?? "Generation did not complete.");
    }
    await new Promise((resolve) => setTimeout(resolve, 5_000));
  }
}
```

On success, use `outputs`:

```ts
const completed = await waitForTask(task.task_id);
const output = completed.outputs[0];

if (output.type === "image") {
  imageElement.src = output.url!;
} else {
  videoElement.src = output.url!;
  posterElement.src = output.thumbnail_url ?? "";
}
```

`thumbnail_url` is supplied only for video when Google Flow provides one. It is absent or `null` for images.

## Request helper on your backend

Keep this server-side. It normalizes both synchronous HTTP errors and successful task requests.

```ts
const baseUrl = "https://api.shopcongngheso5.io.vn";

async function providerFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(baseUrl + path, {
    ...init,
    headers: {
      Authorization: `Bearer ${process.env.FLOW_PROVIDER_API_KEY!}`,
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `FlowProvider request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function providerFetchRaw(path: string): Promise<Response> {
  const response = await fetch(baseUrl + path, {
    headers: { Authorization: `Bearer ${process.env.FLOW_PROVIDER_API_KEY!}` },
  });
  if (!response.ok) throw new Error(`Media fetch failed (${response.status})`);
  return response;
}
```

## Error handling

Synchronous request errors have this envelope:

```json
{
  "error": {
    "status_code": 422,
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [{"field": "start_media_id", "code": "MISSING", "message": "Field required"}],
    "request_id": "req_...",
    "retryable": false
  }
}
```

Use `error.code` and `details[].code` for UI decisions. Task failures are different: `GET /v1/tasks/{task_id}` still returns HTTP `200`, with `status: "failed"` and the same error object nested under `error`.

Typical frontend behavior:

- `400` / `422`: show input validation feedback; do not retry automatically.
- `401`: your backend API key is invalid or missing; alert operators, not end users.
- `429`: back off the request.
- `503 PROVIDER_ACCOUNT_UNAVAILABLE`: keep polling an existing task; it will retry when a video-capable account is available.
- `429 RESOURCE_EXHAUSTED` or `403 PERMISSION_DENIED` inside a failed task: display a retry option and preserve `request_id` for support.

## Useful endpoints

| Endpoint | Frontend use |
|---|---|
| `POST /v1/media` | Upload an image/video reference and receive `media_id` |
| `GET /v1/media/{media_id}` | Retrieve media metadata for the same API client |
| `POST /v1/images/generations` | Create an image task |
| `POST /v1/videos/image-to-video` | Create a single-image video task |
| `POST /v1/videos/omni-generations` | Create a multi-reference video task |
| `GET /v1/tasks/{task_id}` | Poll one task |
| `POST /v1/tasks/{task_id}/cancel` | Request cooperative cancellation |
| `GET /v1/health` | Operational display only; not a user-facing feature |

Provider-account and extension administration endpoints are not frontend endpoints.
