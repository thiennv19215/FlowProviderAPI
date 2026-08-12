from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from sqlalchemy import select

import app.assets.service as asset_service_module
from app.db.models import ApiClient, MediaAsset, ProjectMediaMapping
from app.providers.base import ProviderMedia
from app.providers.google_flow.sdk.helpers import media_entries


def test_provider_url_is_metadata_only_and_referenceable(client,app):
    async def storage_must_not_be_called(*args,**kwargs):
        raise AssertionError("direct provider output must not be copied to storage")

    original_put_bytes=app.state.runtime.storage.put_bytes
    original_put_file=app.state.runtime.storage.put_file
    app.state.runtime.storage.put_bytes=storage_must_not_be_called
    app.state.runtime.storage.put_file=storage_must_not_be_called
    direct_url="https://lh3.googleusercontent.com/generated/image.png?token=opaque"
    try:
        with app.state.runtime.session_factory() as db:
            client_id=db.scalar(select(ApiClient.id))
            asset=asyncio.run(app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_id,
                job_id="job_direct",
                provider="google_flow",
                media=ProviderMedia(media_id="flow-media-1",url=direct_url,mime_type="image/png"),
                asset_type="image",
                provider_project_id="flow-project-1",
            ))
            assert asset.external_url==direct_url
            assert asset.storage_key is None
            mapping=db.scalar(select(ProjectMediaMapping).where(ProjectMediaMapping.asset_id==asset.id))
            assert mapping.provider_media_id=="flow-media-1"
            assert app.state.runtime.assets.content_url(asset)==direct_url
    finally:
        app.state.runtime.storage.put_bytes=original_put_bytes
        app.state.runtime.storage.put_file=original_put_file


def test_provider_url_must_be_allowlisted(client,app):
    with app.state.runtime.session_factory() as db:
        client_id=db.scalar(select(ApiClient.id))
        with pytest.raises(ValueError,match="provider_output_url_not_allowed"):
            asyncio.run(app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_id,
                job_id="job_bad_url",
                provider="google_flow",
                media=ProviderMedia(media_id="bad",url="https://example.org/not-flow.png",mime_type="image/png"),
                asset_type="image",
                provider_project_id="flow-project-1",
            ))


def test_uploaded_assets_still_use_local_storage(client,app,auth):
    created=client.post(
        "/v1/assets/uploads",
        headers=auth,
        json={"filename":"reference.png","content_type":"image/png","type":"image"},
    ).json()
    asset_id=created["asset"]["id"]
    assert client.put(
        f"/v1/assets/{asset_id}/content",
        headers={**auth,"Content-Type":"application/octet-stream"},
        content=b"input-reference",
    ).status_code==204
    with app.state.runtime.session_factory() as db:
        asset=db.get(MediaAsset,asset_id)
        assert asset.external_url is None
        assert asset.storage_key
        assert asyncio.run(app.state.runtime.assets.bytes_for_asset(asset))==b"input-reference"


def test_cross_project_reference_fetches_direct_url_only_when_needed(client,app,monkeypatch):
    requests=[]
    def handler(request:httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(200,content=b"generated-image",headers={"Content-Type":"image/png"})
    real_client=httpx.AsyncClient
    def client_factory(*args,**kwargs):
        kwargs["transport"]=httpx.MockTransport(handler)
        return real_client(*args,**kwargs)
    monkeypatch.setattr(asset_service_module.httpx,"AsyncClient",client_factory)

    class UploadSDK:
        calls=0
        async def upload_image(self,image_base64,mime_type,project_id,file_name):
            self.calls+=1
            assert base64.b64decode(image_base64)==b"generated-image"
            assert project_id=="flow-project-2"
            return {"media_id":"flow-media-copy"}

    with app.state.runtime.session_factory() as db:
        client_id=db.scalar(select(ApiClient.id))
        asset=asyncio.run(app.state.runtime.assets.ingest_provider_media(
            db,
            client_id=client_id,
            job_id="job_cross_project",
            provider="google_flow",
            media=ProviderMedia(media_id="flow-media-source",url="https://lh3.googleusercontent.com/generated/source.png",mime_type="image/png"),
            asset_type="image",
            provider_project_id="flow-project-1",
        ))
        sdk=UploadSDK()
        copied_media_id=asyncio.run(app.state.runtime.providers.get("google_flow").media_sync.ensure_media(
            db,
            client_id=client_id,
            asset_id=asset.id,
            project_id="flow-project-2",
            sdk=sdk,
        ))
        assert copied_media_id=="flow-media-copy"
        assert sdk.calls==1
        assert requests==["https://lh3.googleusercontent.com/generated/source.png"]


def test_external_reference_redirect_is_validated_before_following(client,app,monkeypatch):
    requests=[]
    def handler(request:httpx.Request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={"Location":"https://example.org/internal"})
    real_client=httpx.AsyncClient
    def client_factory(*args,**kwargs):
        kwargs["transport"]=httpx.MockTransport(handler)
        return real_client(*args,**kwargs)
    monkeypatch.setattr(asset_service_module.httpx,"AsyncClient",client_factory)

    with pytest.raises(ValueError,match="external_asset_redirect_not_allowed"):
        asyncio.run(app.state.runtime.assets._external_bytes(
            "https://labs.google/fx/media/source",
            1024,
        ))
    assert requests==["https://labs.google/fx/media/source"]


def test_generated_image_prefers_full_url_over_thumbnail():
    parsed=media_entries({"data":{"media":[{
        "name":"flow-image-1",
        "downloadUrl":None,
        "thumbnailUrl":"https://lh3.googleusercontent.com/thumb.png",
        "image":{"generatedImage":{"fifeUrl":"https://lh3.googleusercontent.com/full.png"}},
    }]}})
    assert parsed[0]["url"]=="https://lh3.googleusercontent.com/full.png"
