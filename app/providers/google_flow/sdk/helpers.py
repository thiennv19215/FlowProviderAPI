from __future__ import annotations
from typing import Any
from app.providers.base import ProviderError
from app.providers.google_flow.sdk.constants import *


def resolve_image_model(key: str | None) -> str:
    return IMAGE_MODELS.get(key or DEFAULT_IMAGE_MODEL_KEY, IMAGE_MODELS[DEFAULT_IMAGE_MODEL_KEY])


def resolve_video_model(tier: str, aspect: str, quality: str | None) -> str | None:
    tier_map = VIDEO_MODEL_KEYS.get(tier) or VIDEO_MODEL_KEYS["PAYGATE_TIER_ONE"]
    quality_map = tier_map.get((quality or "lite").lower()) or tier_map.get("lite") or {}
    return quality_map.get(aspect)


def client_context(project_id: str, tier: str) -> dict:
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid paygate tier: {tier}")
    return {"projectId": project_id, "recaptchaContext": {"applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB", "token": ""}, "sessionId": f";{unique_token()}", "tool": "PINHOLE", "userPaygateTier": tier}


def extract_project_id(resp: Any) -> str | None:
    try: return resp["data"]["result"]["data"]["json"]["result"]["projectId"]
    except Exception: return None


def extract_upload_media_id(resp: Any) -> str | None:
    try: return resp["data"]["media"]["name"]
    except Exception: return None


def inner_error(resp: Any) -> str | None:
    error=flow_error(resp)
    return error.message if error else None


def flow_error(resp: Any) -> ProviderError | None:
    if not isinstance(resp, dict): return None
    status=resp.get("status")
    data=resp.get("data") if isinstance(resp.get("data"),dict) else {}
    err=data.get("error") if isinstance(data,dict) else None
    if isinstance(err,dict) and isinstance(err.get("code"),int):status=err["code"]
    if resp.get("error") or isinstance(status,int) and status>=400 or isinstance(err,dict):
        details=err.get("details") or [] if isinstance(err,dict) else []
        reason=next((d.get("reason") for d in details if isinstance(d,dict) and d.get("reason")),None)
        flow_code=err.get("status") if isinstance(err,dict) else None
        code=str(flow_code or reason or (f"FLOW_HTTP_{status}" if isinstance(status,int) else "FLOW_PROVIDER_ERROR"))
        raw_message=err.get("message") if isinstance(err,dict) else None
        raw_message=raw_message or resp.get("error")
        if not raw_message and isinstance(resp.get("text"),str):
            text=" ".join(resp["text"].split())
            raw_message=text if text and "<" not in text else None
        message=str(raw_message or code)[:1000]
        retryable=status in {408,425,429,500,502,503,504} if isinstance(status,int) else False
        public_details=[]
        for item in details:
            if not isinstance(item,dict): continue
            detail_code=str(item.get("reason") or item.get("status") or "UPSTREAM_DETAIL")[:120]
            detail_message=str(item.get("message") or item.get("localizedMessage") or detail_code)[:500]
            public_details.append({"field":None,"code":detail_code,"message":detail_message})
        return ProviderError(code,message,status_code=status if isinstance(status,int) else None,retryable=retryable,details=public_details)
    return None


def media_entries(resp: Any) -> list[dict]:
    data=resp.get("data") if isinstance(resp,dict) else None
    items=data.get("media") if isinstance(data,dict) else None
    out=[]
    for item in items or []:
        if not isinstance(item,dict) or not isinstance(item.get("name"),str): continue
        url=item.get("downloadUrl")
        image=item.get("image") if isinstance(item.get("image"),dict) else {}
        generated=image.get("generatedImage") if isinstance(image.get("generatedImage"),dict) else {}
        url=url or generated.get("fifeUrl") or generated.get("url") or item.get("thumbnailUrl")
        out.append({"media_id":item["name"],"url":url,"mediaType":item.get("mediaFormat") or "image"})
    return out


def operation_names(resp: Any) -> list[str]:
    data=resp.get("data") if isinstance(resp,dict) else None
    if not isinstance(data,dict): return []
    names=[]
    for op in data.get("operations") or []:
        if isinstance(op,dict):
            inner=op.get("operation") if isinstance(op.get("operation"),dict) else op
            if isinstance(inner.get("name"),str): names.append(inner["name"])
    if names: return names
    for wf in data.get("workflows") or []:
        if isinstance(wf,dict) and isinstance(wf.get("name"),str): names.append(wf["name"])
    if names: return names
    for m in data.get("media") or []:
        if isinstance(m,dict) and isinstance(m.get("name"),str): names.append(m["name"])
    return names


def workflows(resp: Any) -> list[dict]:
    data=resp.get("data") if isinstance(resp,dict) else None
    out=[]
    for wf in (data.get("workflows") if isinstance(data,dict) else []) or []:
        meta=wf.get("metadata") if isinstance(wf,dict) and isinstance(wf.get("metadata"),dict) else {}
        if isinstance(wf,dict) and wf.get("name") and meta.get("primaryMediaId"):
            out.append({"name":wf["name"],"primary_media_id":meta["primaryMediaId"]})
    return out


def poll_operations(resp: Any, requested: list[str]) -> list[dict]:
    data=resp.get("data") if isinstance(resp,dict) else None
    by={}
    if isinstance(data,dict):
        for op in data.get("operations") or []:
            if not isinstance(op,dict): continue
            inner=op.get("operation") if isinstance(op.get("operation"),dict) else op
            name=inner.get("name") if isinstance(inner,dict) else None
            if not name: continue
            status=op.get("status") or inner.get("status")
            meta=inner.get("metadata") if isinstance(inner.get("metadata"),dict) else {}
            vm=meta.get("video") if isinstance(meta.get("video"),dict) else {}
            mid=vm.get("mediaId"); url=vm.get("fifeUrl")
            failed=bool(status and any(x in str(status) for x in ("FAIL","ERROR","CANCEL")))
            done=failed or bool(inner.get("done")) or bool(status and any(x in str(status) for x in ("SUCCESS","SUCCEED","COMPLETE","DONE"))) or bool(mid and url)
            by[name]={"name":name,"done":done,"error":str(status) if failed else None,"media_entries":[{"media_id":mid,"url":url,"mediaType":"video"}] if done and not failed and mid else []}
        for item in data.get("media") or []:
            if not isinstance(item,dict) or not item.get("name"): continue
            name=item["name"]; meta=item.get("mediaMetadata") if isinstance(item.get("mediaMetadata"),dict) else {}; ms=meta.get("mediaStatus") if isinstance(meta.get("mediaStatus"),dict) else {}; status=ms.get("mediaGenerationStatus")
            failed=bool(status and any(x in str(status) for x in ("FAIL","ERROR","CANCEL")))
            done=failed or bool(ms.get("done")) or bool(item.get("downloadUrl")) or bool(status and any(x in str(status) for x in ("SUCCESS","SUCCEED","COMPLETE","DONE")))
            by[name]={"name":name,"done":done,"error":str(status) if failed else None,"media_entries":[{"media_id":name,"url":item.get("downloadUrl"),"mediaType":"video"}] if done and not failed else []}
    return [by.get(name,{"name":name,"done":False,"error":None,"media_entries":[]}) for name in requested]
