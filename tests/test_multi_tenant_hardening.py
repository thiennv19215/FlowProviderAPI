from datetime import datetime, timezone

from sqlalchemy import select

from app.auth.api_keys import hash_api_key, key_prefix
from app.db.models import ApiClient, GenerationJob
from app.ids import new_id


def _regular_auth(app):
    raw="fpa_regular_client"
    db=app.state.runtime.session_factory()
    try:
        db.add(ApiClient(
            id=new_id("cli"),
            name="Regular client",
            key_prefix=key_prefix(raw),
            key_hash=hash_api_key(raw),
            is_admin=False,
            priority=20,
            max_concurrent_jobs=5,
            rate_limit_per_minute=120,
        ))
        db.commit()
    finally:
        db.close()
    return {"Authorization":f"Bearer {raw}"}


def test_regular_client_can_generate_but_cannot_control_provider_pool(client,app):
    auth=_regular_auth(app)
    generated=client.post("/v1/images/generations",headers=auth,json={
        "prompt":"tenant-safe cat","provider":"fake","workspace":{"key":"tenant:regular"}
    })
    assert generated.status_code==202

    accounts=client.get("/v1/accounts",headers=auth)
    assert accounts.status_code==401
    assert accounts.json()["error"]["code"]=="INVALID_ADMIN_KEY"

    extensions=client.get("/v1/extensions",headers=auth)
    assert extensions.status_code==401
    assert extensions.json()["error"]["code"]=="INVALID_ADMIN_KEY"


def test_admin_control_plane_uses_separate_secret(client,auth,admin_auth):
    assert client.get("/v1/accounts",headers=auth).status_code==401
    assert client.get("/v1/accounts",headers=admin_auth).status_code==200
    assert client.get("/v1/extensions",headers=admin_auth).status_code==200


def test_job_cursor_is_stable_when_created_at_timestamps_match(client,app,auth):
    ids=[]
    for i in range(3):
        response=client.post("/v1/images/generations",headers=auth,json={
            "prompt":f"job {i}","provider":"fake","workspace":{"key":f"cursor:{i}"}
        })
        assert response.status_code==202
        ids.append(response.json()["task_id"])

    same_time=datetime(2026,8,12,0,0,0,tzinfo=timezone.utc)
    db=app.state.runtime.session_factory()
    try:
        jobs=list(db.scalars(select(GenerationJob).where(GenerationJob.id.in_(ids))))
        for job in jobs:job.created_at=same_time
        db.commit()
    finally:
        db.close()

    first=client.get("/v1/tasks?limit=2",headers=auth)
    assert first.status_code==200
    first_body=first.json()
    assert first_body["has_more"] is True
    assert len(first_body["data"])==2
    cursor=first_body["next_cursor"]

    second=client.get(f"/v1/tasks?limit=2&after={cursor}",headers=auth)
    assert second.status_code==200
    second_ids=[item["task_id"] for item in second.json()["data"]]
    first_ids=[item["task_id"] for item in first_body["data"]]
    assert len(set(first_ids+second_ids))==3
    assert set(first_ids+second_ids)==set(ids)


def test_job_cursor_from_another_client_is_rejected(client,app,auth):
    admin_job=client.post("/v1/images/generations",headers=auth,json={
        "prompt":"admin","provider":"fake","workspace":{"key":"cursor:admin"}
    }).json()["task_id"]
    regular=_regular_auth(app)
    response=client.get(f"/v1/tasks?after={admin_job}",headers=regular)
    assert response.status_code==400
    assert response.json()["error"]["code"]=="INVALID_CURSOR"
