# Image generation

`POST /v1/images/generations` supports prompt, aspect ratio, output count, optional reference `asset_id`s, workspace key, provider, and model. The Google Flow adapter pins a provider account, ensures an account-specific Flow project, uploads missing project-local references, generates, and stores outputs as Provider assets.
