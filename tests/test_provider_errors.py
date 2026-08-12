import asyncio

import pytest

from app.providers.base import ProviderError
from app.providers.google_flow.sdk.helpers import flow_error


class UpstreamFailureProvider:
    requires_account_pool = False

    def __init__(self,status_code: int,code: str,message: str,retryable: bool):
        self.name=f"upstream_{status_code}"
        self.error=ProviderError(code,message,status_code=status_code,retryable=retryable)

    async def generate_image(self,*,job,db,account_id):
        job.stage="dispatching";db.commit()
        raise self.error

    async def dispatch_video(self,**kwargs):raise AssertionError("not used")
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):raise AssertionError("not used")


@pytest.mark.parametrize(("status_code","code","message","retryable"),[
    (429,"RESOURCE_EXHAUSTED","Quota exceeded.",True),
    (403,"PERMISSION_DENIED","Permission denied.",False),
])
def test_task_preserves_flow_error(client,app,auth,status_code,code,message,retryable):
    provider=UpstreamFailureProvider(status_code,code,message,retryable)
    app.state.runtime.providers.register(provider)
    created=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":provider.name})
    assert created.status_code==202
    assert asyncio.run(app.state.runtime.worker.run_once()) is True

    response=client.get(f"/v1/tasks/{created.json()['task_id']}",headers=auth)
    assert response.status_code==200
    assert response.json()["status"]=="failed"
    assert response.json()["error"]=={
        "status_code":status_code,
        "code":code,
        "message":message,
        "details":[],
        "request_id":created.headers["X-Request-Id"],
        "retryable":retryable,
    }


def test_google_error_parser_keeps_http_status_and_flow_code():
    error=flow_error({"status":429,"data":{"error":{"code":429,"message":"Quota exceeded.","status":"RESOURCE_EXHAUSTED","details":[{"reason":"RATE_LIMIT_EXCEEDED","message":"Image generation quota is exhausted.","internal":"never expose this"}]}}})
    assert error is not None
    assert error.status_code==429
    assert error.code=="RESOURCE_EXHAUSTED"
    assert error.message=="Quota exceeded."
    assert error.retryable is True
    assert error.details==[{"field":None,"code":"RATE_LIMIT_EXCEEDED","message":"Image generation quota is exhausted."}]


def test_google_error_parser_keeps_plain_text_upstream_message():
    error=flow_error({"status":502,"text":"Google Flow is temporarily unavailable."})
    assert error is not None
    assert error.code=="FLOW_HTTP_502"
    assert error.message=="Google Flow is temporarily unavailable."
    assert error.retryable is True


def test_task_returns_sanitized_upstream_error_details(client,app,auth):
    provider=UpstreamFailureProvider(429,"RESOURCE_EXHAUSTED","Quota exceeded.",True)
    provider.error.details=[{"field":None,"code":"RATE_LIMIT_EXCEEDED","message":"Image generation quota is exhausted."}]
    app.state.runtime.providers.register(provider)
    created=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":provider.name})
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    error=client.get(f"/v1/tasks/{created.json()['task_id']}",headers=auth).json()["error"]
    assert error["details"]==[{"field":None,"code":"RATE_LIMIT_EXCEEDED","message":"Image generation quota is exhausted."}]


def test_failed_task_keeps_submission_request_id(client,app,auth):
    provider=UpstreamFailureProvider(503,"UNAVAILABLE","Flow is temporarily unavailable.",True)
    app.state.runtime.providers.register(provider)
    created=client.post("/v1/images/generations",headers={**auth,"X-Request-Id":"req_generation_trace"},json={"prompt":"cat","provider":provider.name})
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    error=client.get(f"/v1/tasks/{created.json()['task_id']}",headers=auth).json()["error"]
    assert error["request_id"]=="req_generation_trace"
