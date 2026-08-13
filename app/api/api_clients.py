from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select

from app.api.deps import get_admin_client, get_db
from app.api.errors import APIError
from app.api.schemas import ApiClientCreate, ApiClientCreated, ApiClientOutput
from app.auth.api_keys import generate_api_key, hash_api_key, key_prefix
from app.db.models import ApiClient
from app.ids import new_id

router = APIRouter(
    prefix="/v1/api-clients",
    tags=["API clients"],
    dependencies=[Depends(get_admin_client)],
)


def _output(client: ApiClient) -> ApiClientOutput:
    return ApiClientOutput.model_validate(client)


@router.post("", response_model=ApiClientCreated, status_code=status.HTTP_201_CREATED)
def create_api_client(payload: ApiClientCreate, db=Depends(get_db)):
    api_key = generate_api_key()
    client = ApiClient(
        id=new_id("cli"),
        name=payload.name,
        key_prefix=key_prefix(api_key),
        key_hash=hash_api_key(api_key),
        priority=payload.priority,
        max_concurrent_jobs=payload.max_concurrent_jobs,
        rate_limit_per_minute=payload.rate_limit_per_minute,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return ApiClientCreated(**_output(client).model_dump(), api_key=api_key)


@router.get("")
def list_api_clients(db=Depends(get_db)):
    clients = db.scalars(select(ApiClient).order_by(ApiClient.created_at.desc(), ApiClient.id)).all()
    return {
        "object": "list",
        "data": [_output(client) for client in clients],
        "has_more": False,
        "next_cursor": None,
    }


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_client(client_id: str, db=Depends(get_db)):
    client = db.get(ApiClient, client_id)
    if client is None:
        raise APIError(404, "API_CLIENT_NOT_FOUND", "The requested API client does not exist.")
    client.enabled = False
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{client_id}/rotate", response_model=ApiClientCreated)
def rotate_api_client_key(client_id: str, db=Depends(get_db)):
    client = db.get(ApiClient, client_id)
    if client is None:
        raise APIError(404, "API_CLIENT_NOT_FOUND", "The requested API client does not exist.")
    api_key = generate_api_key()
    client.key_prefix = key_prefix(api_key)
    client.key_hash = hash_api_key(api_key)
    client.enabled = True
    db.commit()
    db.refresh(client)
    return ApiClientCreated(**_output(client).model_dump(), api_key=api_key)
