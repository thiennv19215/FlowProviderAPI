from __future__ import annotations

import json
import uuid
from urllib.parse import quote

from app.providers.google_flow.sdk.constants import *
from app.providers.google_flow.sdk.helpers import *


class FlowSDK:
    def __init__(self, client):
        self.client=client

    async def create_project(self, title: str) -> dict:
        resp=await self.client.trpc_request(url=TRPC_CREATE_PROJECT,method="POST",headers=TRPC_HEADERS,body={"json":{"projectTitle":title,"toolName":"PINHOLE"}})
        if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
        pid=extract_project_id(resp)
        return {"project_id":pid,"raw":resp} if pid else {"error":"no_project_id_in_response","raw":resp}

    async def search_user_projects(self, cursor: str|None=None, page_size: int=20) -> dict:
        payload={"json":{"pageSize":page_size,"toolName":"PINHOLE","cursor":cursor}}
        if cursor is None: payload["meta"]={"values":{"cursor":["undefined"]}}
        url=TRPC_SEARCH_PROJECTS+"?input="+quote(json.dumps(payload,separators=(",",":")))
        return await self.client.trpc_request(url=url,method="GET",headers=TRPC_HEADERS)

    async def upload_image(self, image_base64: str, mime_type: str, project_id: str, file_name: str="upload.png") -> dict:
        body={"clientContext":{"projectId":project_id,"tool":"PINHOLE"},"fileName":file_name,"imageBytes":image_base64,"isHidden":False,"isUserUploaded":True,"mimeType":mime_type}
        resp=await self.client.api_request(url=UPLOAD_IMAGE_URL,method="POST",headers=API_HEADERS,body=body)
        if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
        mid=extract_upload_media_id(resp)
        return {"media_id":mid,"raw":resp} if mid else {"error":"no_media_id_in_upload_response","raw":resp}

    async def gen_image(self, *, prompt: str, project_id: str, paygate_tier: str, aspect_ratio: str, ref_media_ids: list[str]|None=None, variant_count: int=1, image_model: str|None=None) -> dict:
        n=max(1,min(int(variant_count),MAX_VARIANT_COUNT)); token=unique_token(); ctx=client_context(project_id,paygate_tier); inputs=[{"name":m,"imageInputType":"IMAGE_INPUT_TYPE_REFERENCE"} for m in (ref_media_ids or [])]
        requests=[]
        for i in range(n):
            item={"clientContext":{**ctx,"sessionId":f";{token+i}"},"seed":(token+i*9973)%1_000_000,"structuredPrompt":{"parts":[{"text":prompt}]},"imageAspectRatio":aspect_ratio,"imageModelName":resolve_image_model(image_model)}
            if inputs: item["imageInputs"]=inputs
            requests.append(item)
        body={"clientContext":ctx,"mediaGenerationContext":{"batchId":str(uuid.uuid4())},"useNewMedia":True,"requests":requests}
        url=f"{FLOW_API_BASE}/v1/projects/{project_id}/flowMedia:batchGenerateImages"
        resp=await self.client.api_request(url=url,method="POST",headers=API_HEADERS,body=body,captcha_action=CAPTCHA_IMAGE)
        if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
        entries=media_entries(resp)
        for entry in entries:
            if not entry.get("url") and entry.get("media_id"):
                entry["url"]=await self.client.resolve_media_url(entry["media_id"])
        return {"media_entries":entries,"media_ids":[e["media_id"] for e in entries],"raw":resp}

    async def gen_video(self, *, prompt: str, project_id: str, start_media_id: str, aspect_ratio: str, paygate_tier: str, video_quality: str="lite") -> dict:
        model=resolve_video_model(paygate_tier,aspect_ratio,video_quality)
        if not model: return {"error":"no_video_model_for_request"}
        ctx=client_context(project_id,paygate_tier); token=unique_token()
        body={"clientContext":ctx,"mediaGenerationContext":{"batchId":str(uuid.uuid4())},"requests":[{"aspectRatio":aspect_ratio,"seed":token%1_000_000,"textInput":{"prompt":prompt},"videoModelKey":model,"startImage":{"mediaId":start_media_id},"metadata":{"sceneId":str(uuid.uuid4())}}],"useV2ModelConfig":True}
        resp=await self.client.api_request(url=VIDEO_I2V_URL,method="POST",headers=API_HEADERS,body=body,captcha_action=CAPTCHA_VIDEO)
        if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
        names=operation_names(resp)
        return {"operation_names":names,"workflows":workflows(resp),"raw":resp} if names else {"error":"no_operations_in_response","raw":resp}

    async def gen_video_omni(self, *, prompt: str, project_id: str, ref_media_ids: list[str], duration_s: int, aspect_ratio: str, paygate_tier: str) -> dict:
        model=OMNI_FLASH_DURATION_KEYS.get(duration_s)
        if not model: return {"error":"unsupported_omni_duration"}
        ctx=client_context(project_id,paygate_tier); token=unique_token()
        body={"mediaGenerationContext":{"batchId":str(uuid.uuid4()),"audioFailurePreference":"BLOCK_SILENCED_VIDEOS"},"clientContext":{**ctx,"sessionId":f";{token}"},"requests":[{"aspectRatio":aspect_ratio,"textInput":{"prompt":prompt},"videoModelKey":model,"seed":token%1_000_000,"metadata":{},"referenceImages":[{"mediaId":m,"imageUsageType":"IMAGE_USAGE_TYPE_ASSET"} for m in ref_media_ids]}],"useV2ModelConfig":True}
        resp=await self.client.api_request(url=VIDEO_OMNI_URL,method="POST",headers=API_HEADERS,body=body,captcha_action=CAPTCHA_VIDEO)
        if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
        names=operation_names(resp)
        return {"operation_names":names,"workflows":workflows(resp),"raw":resp} if names else {"error":"no_operations_in_response","raw":resp}

    async def check_async(self, *, operation_names: list[str], project_id: str, workflows_data: list[dict]|None=None) -> dict:
        wf_map={w["name"]:w["primary_media_id"] for w in (workflows_data or []) if w.get("name") and w.get("primary_media_id")}
        media_names=[wf_map[name] for name in operation_names if name in wf_map]
        regular=[name for name in operation_names if name not in wf_map]
        summaries=[]
        if media_names:
            resp=await self.client.api_request(url=VIDEO_POLL_URL,method="POST",headers=API_HEADERS,body={"media":[{"name":m,"projectId":project_id} for m in media_names]})
            if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
            parsed=poll_operations(resp,media_names);reverse={v:k for k,v in wf_map.items()}
            for p in parsed: p["name"]=reverse.get(p["name"],p["name"])
            summaries.extend(parsed)
        if regular:
            resp=await self.client.api_request(url=VIDEO_POLL_URL,method="POST",headers=API_HEADERS,body={"operations":[{"operation":{"name":n}} for n in regular]})
            if (err:=flow_error(resp)): return {"error":err.message,"exception":err,"raw":resp}
            summaries.extend(poll_operations(resp,regular))
        order={name:i for i,name in enumerate(operation_names)}; summaries.sort(key=lambda x:order.get(x.get("name"),99999))
        for op in summaries:
            if op.get("done") and not op.get("error"):
                for entry in op.get("media_entries") or []:
                    if not entry.get("url") and entry.get("media_id"):
                        entry["url"]=await self.client.resolve_media_url(entry["media_id"])
        return {"operations":summaries}
