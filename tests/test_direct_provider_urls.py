from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from sqlalchemy import select
from conftest import upload_media

import app.assets.service as asset_service_module
from app.db.models import ApiClient, MediaAsset, ProjectMediaMapping
from app.providers.base import ProviderMedia
from app.providers.google_flow.sdk.helpers import media_entries
from app.providers.google_flow.sdk import FlowSDK


def _mock_provider_download(monkeypatch, data: bytes, content_type: str = "image/png"):
    requests: list[str] = []

    def handler(request: httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(200, content=data, headers={"Content-Type": content_type})

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(asset_service_module.httpx, "AsyncClient", client_factory)
    return requests


def test_provider_url_is_copied_into_owned_storage_and_referenceable(client, app, monkeypatch):
    direct_url = "https://lh3.googleusercontent.com/generated/image.png?token=opaque"
    requests = _mock_provider_download(monkeypatch, b"generated-image")

    with app.state.runtime.session_factory() as db:
        client_id = db.scalar(select(ApiClient.id))
        asset = asyncio.run(
            app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_id,
                job_id="job_owned",
                provider="google_flow",
                media=ProviderMedia(
                    media_id="flow-media-1",
                    url=direct_url,
                    mime_type="image/png",
                ),
                asset_type="image",
                provider_project_id="flow-project-1",
            )
        )
        assert asset.external_url is None
        assert asset.storage_key
        assert asset.checksum_sha256
        assert asyncio.run(app.state.runtime.assets.bytes_for_asset(asset)) == b"generated-image"
        mapping = db.scalar(
            select(ProjectMediaMapping).where(ProjectMediaMapping.asset_id == asset.id)
        )
        assert mapping.provider_media_id == "flow-media-1"
        assert app.state.runtime.assets.content_url(asset).endswith(f"/media/{asset.id}")
    assert requests == [direct_url]


def test_provider_url_must_be_allowlisted(client, app):
    with app.state.runtime.session_factory() as db:
        client_id = db.scalar(select(ApiClient.id))
        with pytest.raises(ValueError, match="provider_output_url_not_allowed"):
            asyncio.run(
                app.state.runtime.assets.ingest_provider_media(
                    db,
                    client_id=client_id,
                    job_id="job_bad_url",
                    provider="google_flow",
                    media=ProviderMedia(
                        media_id="bad",
                        url="https://example.org/not-flow.png",
                        mime_type="image/png",
                    ),
                    asset_type="image",
                    provider_project_id="flow-project-1",
                )
            )


def test_uploaded_assets_use_provider_storage(client, app, auth):
    asset_id = upload_media(
        client,
        auth,
        filename="reference.png",
        data=b"input-reference",
        content_type="image/png",
    )
    with app.state.runtime.session_factory() as db:
        asset = db.get(MediaAsset, asset_id)
        assert asset.external_url is None
        assert asset.storage_key
        assert asyncio.run(app.state.runtime.assets.bytes_for_asset(asset)) == b"input-reference"


def test_cross_project_reference_reuses_owned_copy_without_refetch(client, app, monkeypatch):
    source_url = "https://lh3.googleusercontent.com/generated/source.png"
    requests = _mock_provider_download(monkeypatch, b"generated-image")

    class UploadSDK:
        calls = 0

        async def upload_image(self, image_base64, mime_type, project_id, file_name):
            self.calls += 1
            assert base64.b64decode(image_base64) == b"generated-image"
            assert project_id == "flow-project-2"
            return {"media_id": "flow-media-copy"}

    with app.state.runtime.session_factory() as db:
        client_id = db.scalar(select(ApiClient.id))
        asset = asyncio.run(
            app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_id,
                job_id="job_cross_project",
                provider="google_flow",
                media=ProviderMedia(
                    media_id="flow-media-source",
                    url=source_url,
                    mime_type="image/png",
                ),
                asset_type="image",
                provider_project_id="flow-project-1",
            )
        )
        sdk = UploadSDK()
        copied_media_id = asyncio.run(
            app.state.runtime.providers.get("google_flow").media_sync.ensure_media(
                db,
                client_id=client_id,
                asset_id=asset.id,
                project_id="flow-project-2",
                sdk=sdk,
            )
        )
        assert copied_media_id == "flow-media-copy"
        assert sdk.calls == 1
        # One network fetch when the output first becomes Provider-owned; the
        # cross-project copy reads the durable Provider storage instead.
        assert requests == [source_url]


def test_external_reference_redirect_is_validated_before_following(client, app, monkeypatch):
    requests = []

    def handler(request: httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://example.org/internal"})

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(asset_service_module.httpx, "AsyncClient", client_factory)

    with pytest.raises(ValueError, match="external_asset_redirect_not_allowed"):
        asyncio.run(
            app.state.runtime.assets._external_bytes(
                "https://labs.google/fx/media/source",
                1024,
            )
        )
    assert requests == ["https://labs.google/fx/media/source"]


def test_generated_image_parser_never_promotes_thumbnail_to_output():
    parsed = media_entries(
        {
            "data": {
                "media": [
                    {
                        "name": "flow-image-1",
                        "downloadUrl": None,
                        "thumbnailUrl": "https://lh3.googleusercontent.com/thumb.png",
                        "image": {
                            "generatedImage": {
                                "fifeUrl": "https://lh3.googleusercontent.com/full.png"
                            }
                        },
                    }
                ]
            }
        }
    )
    assert "url" not in parsed[0]
    assert parsed[0]["generated_url"] == "https://lh3.googleusercontent.com/full.png"
    assert parsed[0]["thumbnail_url"] == "https://lh3.googleusercontent.com/thumb.png"


def test_generated_image_uses_nested_generation_id_not_outer_item_name():
    parsed=media_entries({"data":{"media":[{"name":"outer-media-item","image":{"generatedImage":{"mediaGenerationId":"exact-generated-image","fifeUrl":"https://lh3.googleusercontent.com/exact.jpg"}}}]}})
    assert parsed[0]["media_id"]=="exact-generated-image"
    assert parsed[0]["generated_url"]=="https://lh3.googleusercontent.com/exact.jpg"


def test_generated_image_parser_does_not_use_thumbnail_without_full_url():
    parsed = media_entries(
        {
            "data": {
                "media": [
                    {
                        "name": "flow-image-thumbnail-only",
                        "downloadUrl": None,
                        "thumbnailUrl": "https://lh3.googleusercontent.com/thumb.png",
                    }
                ]
            }
        }
    )
    assert "url" not in parsed[0]
    assert parsed[0]["generated_url"] is None


def test_generated_image_uses_url_from_exact_response_item_before_resolver():
    class Client:
        def __init__(self):
            self.resolved=[]

        async def api_request(self,**kwargs):
            return {
                "data": {
                    "media": [
                        {
                            "name":"flow-image-1",
                            "downloadUrl":None,
                            "thumbnailUrl":"https://lh3.googleusercontent.com/thumb.png",
                            "image":{"generatedImage":{"fifeUrl":"https://lh3.googleusercontent.com/rendered.png"}},
                        }
                    ]
                }
            }

        async def resolve_media_url(self,media_id,*,thumbnail=False):
            self.resolved.append((media_id,thumbnail))
            return "https://lh3.googleusercontent.com/original.png"

    client=Client()
    result=asyncio.run(FlowSDK(client).gen_image(prompt="cat",project_id="project",paygate_tier="PAYGATE_TIER_ONE",aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",image_model="NANO_BANANA_PRO"))
    assert client.resolved==[]
    assert result["media_entries"][0]["url"]=="https://lh3.googleusercontent.com/rendered.png"
    assert "generated_url" not in result["media_entries"][0]


def test_generated_image_resolves_by_id_only_without_item_output_url():
    class Client:
        def __init__(self):
            self.resolved=[]

        async def api_request(self,**kwargs):
            return {"data":{"media":[{"name":"flow-image-1","thumbnailUrl":"https://lh3.googleusercontent.com/thumb.png"}]}}

        async def resolve_media_url(self,media_id,*,thumbnail=False):
            self.resolved.append((media_id,thumbnail))
            return "https://lh3.googleusercontent.com/resolved.png"

    client=Client()
    result=asyncio.run(FlowSDK(client).gen_image(prompt="cat",project_id="project",paygate_tier="PAYGATE_TIER_ONE",aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",image_model="NANO_BANANA_PRO"))
    assert client.resolved==[("flow-image-1",False)]
    assert result["media_entries"][0]["url"]=="https://lh3.googleusercontent.com/resolved.png"


def test_generated_image_decodes_inline_bytes_when_url_is_absent():
    class Client:
        def __init__(self):
            self.resolved=[]

        async def api_request(self,**kwargs):
            return {"data":{"media":[{"name":"outer","image":{"generatedImage":{"mediaGenerationId":"generated","encodedImage":base64.b64encode(b"exact-image").decode("ascii")}}}]}}

        async def resolve_media_url(self,media_id,*,thumbnail=False):
            self.resolved.append(media_id)
            return "https://lh3.googleusercontent.com/wrong-image.jpg"

    client=Client()
    result=asyncio.run(FlowSDK(client).gen_image(prompt="cat",project_id="project",paygate_tier="PAYGATE_TIER_ONE",aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",image_model="NANO_BANANA_PRO"))
    entry=result["media_entries"][0]
    assert entry["media_id"]=="generated"
    assert entry["url"] is None
    assert entry["bytes_data"]==b"exact-image"
    assert client.resolved==[]


def test_generated_image_prefers_nested_fife_url_over_outer_download_url():
    class Client:
        async def api_request(self,**kwargs):
            return {"data":{"media":[{"name":"outer","downloadUrl":"https://lh3.googleusercontent.com/legacy.jpg","image":{"generatedImage":{"mediaGenerationId":"generated","fifeUrl":"https://lh3.googleusercontent.com/exact.jpg"}}}]}}

        async def resolve_media_url(self,*args,**kwargs):
            raise AssertionError("resolver must not run")

    result=asyncio.run(FlowSDK(Client()).gen_image(prompt="cat",project_id="project",paygate_tier="PAYGATE_TIER_ONE",aspect_ratio="IMAGE_ASPECT_RATIO_SQUARE",image_model="NANO_BANANA_PRO"))
    assert result["media_entries"][0]["url"]=="https://lh3.googleusercontent.com/exact.jpg"


def test_video_output_keeps_thumbnail_metadata(client, app, auth, monkeypatch):
    _mock_provider_download(monkeypatch, b"video-bytes", content_type="video/mp4")
    with app.state.runtime.session_factory() as db:
        client_id = db.scalar(select(ApiClient.id))
        video = asyncio.run(
            app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_id,
                job_id="job_video_thumbnail",
                provider="google_flow",
                media=ProviderMedia(
                    media_id="flow-video",
                    url="https://lh3.googleusercontent.com/video.mp4",
                    thumbnail_url="https://lh3.googleusercontent.com/video-thumb.jpg",
                    mime_type="video/mp4",
                ),
                asset_type="video",
                provider_project_id="flow-project-1",
            )
        )
        assert video.thumbnail_url == "https://lh3.googleusercontent.com/video-thumb.jpg"
        assert video.storage_key
        assert video.external_url is None
