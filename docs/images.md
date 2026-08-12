# Image generation

`POST /v1/images/generations` accepts `prompt`, `model` (`banana_pro` or `banana_2`), `aspect_ratio`, `output_count`, and optional `reference_media_ids`. `banana_pro` and portrait ratio `9:16` are the defaults. The authenticated API client defines the project scope. The Google Flow adapter reuses one project per API client and provider account, uploads missing project-local references, and returns each generated image with its direct Flow URL and a compact media `id`.

New public asset identifiers use the compact URL-safe form `asset_<16 characters>`. Existing longer asset IDs remain valid.

The `url` is hosted by Google Flow and is not copied into FlowProvider storage. Download it promptly if the integrating backend requires a durable copy.
