# Image generation

`POST /v1/images/generations` supports prompt, aspect ratio, output count, optional reference `asset_id`s, provider, and model. The authenticated API client defines the project scope. The Google Flow adapter reuses one project per API client and provider account, uploads missing project-local references, generates, and stores outputs as Provider assets.
