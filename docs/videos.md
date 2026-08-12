# Video generation

Use `POST /v1/videos/image-to-video` for start-image video and `POST /v1/videos/omni-generations` for multi-reference Omni video. Both return `202` tasks. Dispatch and polling are separated so long-running operations survive worker restarts without blind retries.
