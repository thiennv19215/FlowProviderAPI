from conftest import upload_media


def test_local_media_is_streamed_by_api(client, auth):
    media_id = upload_media(
        client,
        auth,
        filename="local.png",
        data=b"local-image",
        content_type="image/png",
    )

    response = client.get(f"/media/{media_id}")

    assert response.status_code == 200
    assert response.content == b"local-image"
    assert response.headers["content-type"] == "image/png"


def test_r2_media_redirects_to_presigned_url(client, app, auth, monkeypatch):
    media_id = upload_media(
        client,
        auth,
        filename="r2.png",
        data=b"r2-image",
        content_type="image/png",
    )
    calls = []

    async def create_download_url(key, *, expires_seconds=None):
        calls.append((key, expires_seconds))
        return "https://r2.example.test/signed-object?signature=test"

    monkeypatch.setattr(
        app.state.runtime.assets.storage,
        "create_download_url",
        create_download_url,
    )

    response = client.get(
        f"/media/{media_id}", follow_redirects=False
    )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://r2.example.test/signed-object?signature=test"
    )
    assert len(calls) == 1
    assert calls[0][0].endswith(f"/{media_id}.png")
    assert calls[0][1] is None


def test_missing_r2_object_falls_back_to_legacy_local(client, app, auth, monkeypatch):
    media_id = upload_media(
        client,
        auth,
        filename="legacy.png",
        data=b"legacy-image",
        content_type="image/png",
    )

    async def create_download_url(key, *, expires_seconds=None):
        return None

    monkeypatch.setattr(
        app.state.runtime.assets.storage,
        "create_download_url",
        create_download_url,
    )

    response = client.get(f"/media/{media_id}")

    assert response.status_code == 200
    assert response.content == b"legacy-image"


def test_media_metadata_still_requires_api_key(client, auth):
    media_id = upload_media(
        client,
        auth,
        filename="private-metadata.png",
        data=b"public-content-private-metadata",
        content_type="image/png",
    )

    response = client.get(f"/v1/media/{media_id}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_API_KEY"
