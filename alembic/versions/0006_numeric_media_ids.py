"""replace legacy public media IDs with numeric IDs

Revision ID: 0006_numeric_media_ids
Revises: 0005_video_thumbnails
Create Date: 2026-08-12
"""

import secrets

from alembic import op
import sqlalchemy as sa


revision = "0006_numeric_media_ids"
down_revision = "0005_video_thumbnails"
branch_labels = None
depends_on = None


def _new_id(used: set[str]) -> str:
    while True:
        value = str(secrets.randbelow(900_000_000_000_000) + 100_000_000_000_000)
        if value not in used:
            used.add(value)
            return value


def _replace_payload_ids(payload, replacements: dict[str, str]):
    if not isinstance(payload, dict):
        return payload
    value = dict(payload)
    if isinstance(value.get("reference_media_ids"), list):
        value["reference_media_ids"] = [replacements.get(item, item) for item in value["reference_media_ids"]]
    if isinstance(value.get("start_media_id"), str):
        value["start_media_id"] = replacements.get(value["start_media_id"], value["start_media_id"])
    if isinstance(value.get("asset_ids"), list):
        value["asset_ids"] = [replacements.get(item, item) for item in value["asset_ids"]]
    return value


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    assets = sa.Table("media_assets", metadata, autoload_with=bind)
    mappings = sa.Table("project_media_mappings", metadata, autoload_with=bind)
    jobs = sa.Table("generation_jobs", metadata, autoload_with=bind)
    existing = set(bind.execute(sa.select(assets.c.id)).scalars())
    replacements = {old: _new_id(existing) for old in list(existing) if not (len(old) == 15 and old.isdigit())}
    for old, new in replacements.items():
        row = dict(bind.execute(sa.select(assets).where(assets.c.id == old)).mappings().one())
        # Create the new parent first so the project-media foreign key always
        # points at an existing row. A storage key is unique, so release it
        # from the legacy row just before cloning it.
        if row.get("storage_key") is not None:
            bind.execute(assets.update().where(assets.c.id == old).values(storage_key=None))
        row["id"] = new
        bind.execute(assets.insert().values(**row))
        bind.execute(mappings.update().where(mappings.c.asset_id == old).values(asset_id=new))
        bind.execute(assets.delete().where(assets.c.id == old))
    if replacements:
        rows = bind.execute(sa.select(jobs.c.id, jobs.c.request_payload, jobs.c.result_payload)).mappings()
        for row in rows:
            request_payload = _replace_payload_ids(row["request_payload"], replacements)
            result_payload = _replace_payload_ids(row["result_payload"], replacements)
            bind.execute(jobs.update().where(jobs.c.id == row["id"]).values(request_payload=request_payload, result_payload=result_payload))


def downgrade() -> None:
    # Numeric public IDs intentionally remain stable; there is no safe reverse
    # mapping once callers have persisted them.
    pass
