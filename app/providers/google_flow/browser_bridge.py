from __future__ import annotations

import copy
import json
from typing import Any

from app.providers.google_flow.client import (
    FLOW_CREDITS_URL,
    RECAPTCHA_FALLBACK_KEY,
    FlowBridge as BaseFlowBridge,
    resolve_paygate_tier,
)


class FlowBridge(BaseFlowBridge):
    """Backend orchestration bridge with browser-owned Flow authentication."""

    async def handle_message(self, data: dict, ws: Any) -> None:
        conn_id = self._ws_to_id.get(id(ws))
        conn = self._connections.get(conn_id) if conn_id else None
        if data.get("type") == "auth_available" and conn:
            conn.last_seen_at = __import__("time").time()
            conn.suspect_since = None
            # A Chrome profile can switch Google accounts without reconnecting
            # the websocket. Never expose the previous identity/balance during
            # the auth_available -> user_info/credits synchronization window.
            conn.account_email = None
            conn.paygate_tier = None
            conn.credits = None
            conn.flow_key = "browser_owned"
            await self._send_auth_ack(conn)
            self.schedule_account_refresh(conn.id)
            return
        await super().handle_message(data, ws)

    async def refresh_account(self, connection_id: str) -> None:
        conn = self.get(connection_id)
        if not conn or not conn.flow_key:
            return
        conn.credits = None
        api_key = conn.flow_api_key or self.flow_api_key
        if not api_key:
            conn.last_error = "flow_api_key_unavailable"
            await self._send_auth_ack(conn)
            return
        spec = {
            "url": f"{FLOW_CREDITS_URL}?key={api_key}",
            "method": "GET",
            "headers": {
                "origin": "https://labs.google",
                "referer": "https://labs.google/",
            },
            "authMode": "flow",
            "responseType": "json",
            "timeoutMs": 30000,
        }
        response = await self.send_rpc(connection_id, "SW_FETCH", {"spec": spec}, timeout=35)
        if response.get("error"):
            conn.last_error = str(response["error"])[:300]
            await self._send_auth_ack(conn)
            return
        inner = response.get("data") if isinstance(response, dict) else None
        if not isinstance(inner, dict) or inner.get("ok") is False:
            status = inner.get("status") if isinstance(inner, dict) else None
            reason = f"flow_credits_http_{status}" if isinstance(status, int) else "flow_credits_unavailable"
            if status in {401, 403}:
                self._invalidate_auth(conn, reason)
            else:
                conn.last_error = reason
            await self._send_auth_ack(conn)
            return
        payload = inner.get("data")
        if isinstance(payload, dict):
            conn.last_error = None
            conn.paygate_tier = resolve_paygate_tier(payload)
            conn.credits = payload.get("credits") if isinstance(payload.get("credits"), int) else None
            conn.sku = payload.get("sku") if isinstance(payload.get("sku"), str) else None
        else:
            conn.last_error = "flow_credits_invalid_response"
        await self._send_auth_ack(conn)

    async def api_request(
        self,
        connection_id: str,
        *,
        url: str,
        method: str = "POST",
        headers: dict | None = None,
        body: Any = None,
        captcha_action: str | None = None,
        timeout: float | None = None,
        response_type: str | None = None,
    ) -> dict:
        conn = self.get(connection_id)
        if not conn:
            return {"error": "extension_disconnected"}
        final_body = copy.deepcopy(body)
        if captcha_action:
            tab_resp = await self.send_rpc(connection_id, "OPEN_FLOW_TAB", {"allowCreateHome": True}, timeout=self.TAB_TIMEOUT)
            tab_data = tab_resp.get("data") if isinstance(tab_resp, dict) else None
            tab_id = tab_data.get("tabId") if isinstance(tab_data, dict) else None
            if not tab_id:
                return {"error": f"flow_tab_open_failed:{tab_resp.get('error') or 'no_tab_id'}"}
            captcha_resp = await self.send_rpc(
                connection_id,
                "INJECT_RECAPTCHA",
                {"tabId": tab_id, "fallbackKey": RECAPTCHA_FALLBACK_KEY, "action": captcha_action},
                timeout=self.CAPTCHA_TIMEOUT,
            )
            token = captcha_resp.get("data") if isinstance(captcha_resp, dict) else None
            if not isinstance(token, str) or not token:
                return {"error": f"captcha_failed:{captcha_resp.get('error') or 'empty_token'}"}
            if isinstance(final_body, dict):
                ctx = final_body.get("clientContext", {}).get("recaptchaContext")
                if isinstance(ctx, dict):
                    ctx["token"] = token
                for item in final_body.get("requests") or []:
                    if isinstance(item, dict):
                        item_ctx = item.get("clientContext", {}).get("recaptchaContext")
                        if isinstance(item_ctx, dict):
                            item_ctx["token"] = token
        fetch_headers = dict(headers or {})
        is_get = method.upper() in {"GET", "HEAD"}
        if is_get:
            fetch_headers = {k: v for k, v in fetch_headers.items() if k.lower() != "content-type"}
        else:
            fetch_headers.setdefault("content-type", "text/plain;charset=UTF-8")
        spec = {
            "url": url,
            "method": method,
            "headers": fetch_headers,
            "authMode": "flow",
            "timeoutMs": int((timeout or self.DEFAULT_TIMEOUT) * 1000),
            "responseType": response_type or "json",
        }
        if final_body is not None and not is_get:
            spec["body"] = json.dumps(final_body)
        response = await self.send_rpc(connection_id, "SW_FETCH", {"spec": spec}, timeout=(timeout or self.DEFAULT_TIMEOUT) + 5)
        return self._normalize_fetch(response, "API")

    async def trpc_request(
        self,
        connection_id: str,
        *,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: Any = None,
        timeout: float = 30,
        response_type: str | None = None,
    ) -> dict:
        conn = self.get(connection_id)
        if not conn:
            return {"error": "extension_disconnected"}
        tab_resp = await self.send_rpc(connection_id, "OPEN_FLOW_TAB", {"allowCreateHome": False}, timeout=self.TAB_TIMEOUT)
        tab_data = tab_resp.get("data") if isinstance(tab_resp, dict) else None
        tab_id = tab_data.get("tabId") if isinstance(tab_data, dict) else None
        if not tab_id:
            return {"error": f"no_labs_tab:{tab_resp.get('error') or 'no_tab_id'}"}
        is_get = method.upper() == "GET"
        fetch_headers = dict(headers or {})
        if not is_get:
            fetch_headers.setdefault("content-type", "application/json")
        spec = {
            "url": url,
            "method": method,
            "headers": fetch_headers,
            "authMode": "flow",
            "timeoutMs": int(timeout * 1000),
        }
        if response_type:
            spec["responseType"] = response_type
        if body is not None and not is_get:
            spec["body"] = json.dumps(body)
        response = await self.send_rpc(connection_id, "INJECT_PAGE_FETCH", {"tabId": tab_id, "spec": spec}, timeout=timeout + 5)
        return self._normalize_fetch(response, "TRPC", final_url_as_data=True)
