from __future__ import annotations
import argparse,secrets
from app.auth.api_keys import hash_api_key,key_prefix
from app.config import get_settings
from app.db.models import ApiClient,Base
from app.db.session import build_engine,build_session_factory
from app.ids import new_id

p=argparse.ArgumentParser();p.add_argument("name");p.add_argument("--priority",type=int,default=20);p.add_argument("--max-concurrent",type=int,default=5);p.add_argument("--rate-limit",type=int,default=120);args=p.parse_args()
settings=get_settings();engine=build_engine(settings.database_url);Base.metadata.create_all(engine);Session=build_session_factory(engine)
api_key="fpa_live_"+secrets.token_urlsafe(32)
with Session() as db:
    row=ApiClient(id=new_id("cli"),name=args.name,key_prefix=key_prefix(api_key),key_hash=hash_api_key(api_key),priority=args.priority,max_concurrent_jobs=args.max_concurrent,rate_limit_per_minute=args.rate_limit)
    db.add(row);db.commit();print(f"client_id={row.id}\napi_key={api_key}\nStore this key now; only its SHA-256 hash is stored.")
