from __future__ import annotations

import threading
from datetime import timedelta, timezone

from sqlalchemy import select, text

from app.db.models import RateLimitBucket, utcnow


class RateLimiter:
    """Database-backed fixed-window limiter; PostgreSQL replicas share one bucket per client."""
    def __init__(self):self._fallback_lock=threading.Lock()

    @staticmethod
    def _pg_lock(db,client_id:str)->None:
        if db.bind and db.bind.dialect.name=="postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),{"key":f"rate-limit:{client_id}"})

    @staticmethod
    def _as_utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def hit(self,db,client_id:str,limit:int)->tuple[bool,int,int]:
        now=utcnow();limit=max(1,int(limit))
        lock=self._fallback_lock if not db.bind or db.bind.dialect.name!="postgresql" else None
        if lock:lock.acquire()
        try:
            self._pg_lock(db,client_id)
            bucket=db.scalar(select(RateLimitBucket).where(RateLimitBucket.client_id==client_id).with_for_update())
            if not bucket:
                bucket=RateLimitBucket(client_id=client_id,window_started_at=now,request_count=0);db.add(bucket);db.flush()
            window_started_at=self._as_utc(bucket.window_started_at)
            if now>=window_started_at+timedelta(seconds=60):
                bucket.window_started_at=now;bucket.request_count=0;window_started_at=now
            allowed=bucket.request_count<limit
            if allowed:bucket.request_count+=1
            remaining=max(0,limit-bucket.request_count)
            reset=int((window_started_at+timedelta(seconds=60)).timestamp())
            db.commit();return allowed,remaining,reset
        finally:
            if lock:lock.release()
