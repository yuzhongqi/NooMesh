"""Bidirectional Teams Workflows interactive Adaptive Card relay (outbound-only)."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import RuntimeErrorBase, Store, canonical, digest, now, require
from .gateway_transport import NoRedirect
from .learning import privacy_key
from .lifecycle import parsed
from .notifier import _acquire_flow_bearer_token, validate_webhook_url
from .privacy import sanitize

FLOW_API_HOST = "api.flow.microsoft.com"


def relay_config_path(store: Store) -> Path:
    return store.home / "relay.json"


def load_relay_config(store: Store) -> Optional[Dict[str, Any]]:
    path = relay_config_path(store)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("webhook_url"):
        return None
    return data


def _cached_bearer_token(store: Store) -> str:
    cache_file = store.home / "flow-token-cache.json"
    clock = datetime.now(timezone.utc)
    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            exp = parsed(cached.get("expires_at"))
            tok = cached.get("token", "")
            if exp and tok and (exp - clock).total_seconds() > 180:
                return tok
        except (OSError, ValueError):
            pass
    token = _acquire_flow_bearer_token()
    try:
        cache_file.write_text(canonical({
            "token": token,
            "expires_at": (clock + timedelta(minutes=25)).isoformat(),
        }), encoding="utf-8")
        cache_file.chmod(0o600)
    except OSError:
        pass
    return token


def configure_relay(store: Store, approve: bool,
                    webhook_url: Optional[str] = None,
                    environment_id: Optional[str] = None,
                    flow_id: Optional[str] = None,
                    enabled: Optional[bool] = None,
                    auto_on_stop: Optional[bool] = None,
                    min_duration_seconds: Optional[int] = None,
                    poll_timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
    require(approve, "Explicit approval required to configure interactive Teams relay")
    existing = load_relay_config(store) or {}
    final_url = (webhook_url.strip() if isinstance(webhook_url, str) and webhook_url.strip()
                 else existing.get("webhook_url"))
    require(bool(final_url), "Supply interactive relay webhook URL")
    checked = validate_webhook_url(final_url)
    env_id = (environment_id.strip() if isinstance(environment_id, str) and environment_id.strip()
              else existing.get("environment_id", ""))
    fid = (flow_id.strip() if isinstance(flow_id, str) and flow_id.strip()
           else existing.get("flow_id", ""))
    require(bool(env_id) and bool(fid), "Supply Power Automate environment_id and flow_id")
    min_dur = (min_duration_seconds if min_duration_seconds is not None
               else existing.get("min_duration_seconds", 15))
    require(type(min_dur) is int and 0 <= min_dur <= 86400, "min_duration_seconds must be 0-86400")
    poll_timeout = (poll_timeout_seconds if poll_timeout_seconds is not None
                    else existing.get("poll_timeout_seconds", 1800))
    require(type(poll_timeout) is int and 10 <= poll_timeout <= 86400,
            "poll_timeout_seconds must be 10-86400")
    record = {
        "schema": "llr.relay.v1",
        "enabled": bool(enabled if enabled is not None else existing.get("enabled", True)),
        "auto_on_stop": bool(auto_on_stop if auto_on_stop is not None
                             else existing.get("auto_on_stop", True)),
        "min_duration_seconds": min_dur,
        "poll_timeout_seconds": poll_timeout,
        "environment_id": env_id,
        "flow_id": fid,
        "webhook_url": checked["url"],
        "masked_url": checked["masked_url"],
        "updated_at": now(),
    }
    target = relay_config_path(store)
    target.write_text(canonical(record), encoding="utf-8")
    target.chmod(0o600)
    return relay_status(store)


def relay_status(store: Store) -> Dict[str, Any]:
    cfg = load_relay_config(store)
    if not cfg:
        return {"configured": False, "enabled": False}
    checked = validate_webhook_url(cfg["webhook_url"])
    return {
        "configured": True,
        "enabled": bool(cfg.get("enabled", True)),
        "auto_on_stop": bool(cfg.get("auto_on_stop", True)),
        "min_duration_seconds": int(cfg.get("min_duration_seconds", 15)),
        "poll_timeout_seconds": int(cfg.get("poll_timeout_seconds", 1800)),
        "environment_id": cfg.get("environment_id"),
        "flow_id": cfg.get("flow_id"),
        "masked_url": checked["masked_url"],
        "updated_at": cfg.get("updated_at"),
    }


def build_interactive_card(store: Store, session: str, heading: str,
                           summary: str, choices: Optional[List[Dict[str, str]]] = None,
                           request_id: Optional[str] = None) -> Dict[str, Any]:
    key = privacy_key(store)
    req_id = request_id or ("req-" + uuid.uuid4().hex[:12])
    safe_heading = sanitize((heading or "VS Code Session")[:140], key)[0]
    safe_summary = sanitize((summary or "Task turn completed.")[:1200], key)[0]
    normalized_choices = []
    if choices:
        for item in choices[:6]:
            label = sanitize(str(item.get("label") or item.get("title") or "")[:80], key)[0]
            val = sanitize(str(item.get("value") or label)[:80], key)[0]
            if label and val:
                normalized_choices.append({"title": label, "value": val})
    if not normalized_choices:
        normalized_choices = [
            {"title": "✅ 继续下一步 (Continue)", "value": "__continue__"},
            {"title": "⏸️ 已阅，暂不继续 (Done for now)", "value": "__stop__"},
        ]
    default_val = normalized_choices[0]["value"] if len(normalized_choices) > 2 else "__stop__"
    if not any(c["value"] == default_val for c in normalized_choices):
        default_val = normalized_choices[0]["value"]
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": safe_heading, "weight": "Bolder",
             "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": safe_summary, "wrap": True},
            {
                "type": "Input.ChoiceSet",
                "id": "choice",
                "style": "expanded",
                "value": default_val,
                "choices": normalized_choices,
            },
            {
                "type": "Input.Text",
                "id": "reply_text",
                "placeholder": "在此输入指令直接发回当前 Session（若填写则优先执行输入内容）...",
                "isMultiline": True,
            },
            {
                "type": "TextBlock",
                "text": f"Session: {session[:12]} · Request: {req_id}",
                "isSubtle": True,
                "size": "Small",
                "wrap": True,
            },
        ],
        "actions": [{
            "type": "Action.Submit",
            "title": "🚀 发送回当前 Session",
            "data": {"request_id": req_id, "session": session[:36]},
        }],
    }
    return {"request_id": req_id, "card": card}


def send_interactive_relay(store: Store, session: str, heading: str,
                           summary: str, choices: Optional[List[Dict[str, str]]] = None,
                           update_message: str = "✅ 已发回电脑当前 Session！") -> Dict[str, Any]:
    cfg = load_relay_config(store)
    require(cfg is not None and cfg.get("enabled", True), "Interactive Teams relay is not enabled")
    checked = validate_webhook_url(cfg["webhook_url"])
    built = build_interactive_card(store, session, heading, summary, choices)
    envelope = {
        "request_id": built["request_id"],
        "card_json": json.dumps(built["card"], ensure_ascii=False),
        "update_message": sanitize(update_message[:120], privacy_key(store))[0],
    }
    raw = canonical(envelope).encode("utf-8")
    require(len(raw) <= 24_000, "Interactive card payload exceeds 24000 bytes")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    parsed_url = urllib.parse.urlparse(checked["url"])
    if parsed_url.scheme == "https" and "sig=" not in parsed_url.query:
        headers["Authorization"] = "Bearer " + _cached_bearer_token(store)
    req = urllib.request.Request(checked["url"], data=raw, headers=headers, method="POST")
    with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as resp:
        status_code = int(getattr(resp, "status", None) or resp.getcode())
        run_id = resp.headers.get("x-ms-workflow-run-id") or resp.headers.get("X-Ms-Workflow-Run-Id") or ""
        resp.read(4096)
    require(200 <= status_code < 300 and bool(run_id),
            f"Interactive relay webhook did not return a valid run_id (HTTP {status_code})")
    with store.transaction():
        store.db.execute(
            "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("relay_active:" + session[:64], canonical({"run_id": run_id, "request_id": built["request_id"], "created_at": now()})))
    return {
        "status": "WAITING",
        "run_id": run_id,
        "request_id": built["request_id"],
        "http_status": status_code,
    }


def _flow_api_get(store: Store, url: str) -> Dict[str, Any]:
    parsed_url = urllib.parse.urlparse(url)
    require(parsed_url.scheme == "https" or parsed_url.hostname in ("127.0.0.1", "localhost"),
            "Flow API URL must use HTTPS")
    headers = {"Accept": "application/json"}
    if parsed_url.hostname == FLOW_API_HOST:
        headers["Authorization"] = "Bearer " + _cached_bearer_token(store)
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read(256_000).decode("utf-8"))


def poll_interactive_run(store: Store, run_id: str,
                         base_api_url: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_relay_config(store)
    require(cfg is not None, "Interactive Teams relay is not configured")
    require(isinstance(run_id, str) and 4 <= len(run_id) <= 128 and "/" not in run_id,
            "Invalid workflow run_id")
    env_id = cfg["environment_id"]
    flow_id = cfg["flow_id"]
    api_root = base_api_url or f"https://{FLOW_API_HOST}/providers/Microsoft.ProcessSimple"
    run_url = f"{api_root}/environments/{env_id}/flows/{flow_id}/runs/{run_id}?api-version=2016-11-01"
    run_data = _flow_api_get(store, run_url)
    run_status = run_data.get("properties", {}).get("status", "Unknown")
    if run_status == "Running":
        return {"status": "RUNNING", "run_id": run_id}
    if run_status != "Succeeded":
        return {"status": run_status.upper(), "run_id": run_id,
                "error": run_data.get("properties", {}).get("error")}
    action_url = f"{api_root}/environments/{env_id}/flows/{flow_id}/runs/{run_id}/actions/Compose_Reply?api-version=2016-11-01"
    action_data = _flow_api_get(store, action_url)
    props = action_data.get("properties", {})
    outputs = props.get("outputs")
    if outputs is None and isinstance(props.get("outputsLink"), dict) and props["outputsLink"].get("uri"):
        outputs = _flow_api_get(store, props["outputsLink"]["uri"])
    outputs = outputs if isinstance(outputs, dict) else {}
    response = outputs.get("response") if isinstance(outputs.get("response"), dict) else outputs
    form_data = response.get("data") if isinstance(response.get("data"), dict) else {}
    choice = str(form_data.get("choice", "")).strip()
    reply_text = str(form_data.get("reply_text", "")).strip()
    responder = ""
    if isinstance(response.get("responder"), dict):
        responder = str(response["responder"].get("userPrincipalName")
                        or response["responder"].get("email") or "")
    prompt_to_send = ""
    if reply_text:
        if choice and choice not in ("__continue__", "__stop__"):
            prompt_to_send = f"[Teams 选择: {choice}] {reply_text}"
        else:
            prompt_to_send = reply_text
    elif choice == "__continue__":
        prompt_to_send = "请继续执行下一步。"
    elif choice and choice != "__stop__":
        prompt_to_send = choice
    return {
        "status": "REPLIED",
        "run_id": run_id,
        "request_id": outputs.get("request_id") or form_data.get("request_id"),
        "choice": choice,
        "reply_text": reply_text,
        "should_continue": bool(prompt_to_send),
        "prompt": prompt_to_send,
        "responder": responder,
    }


def wait_for_interactive_run(store: Store, run_id: str,
                             timeout_seconds: int = 300,
                             poll_interval: float = 2.5) -> Dict[str, Any]:
    require(5 <= timeout_seconds <= 7200, "Wait timeout must be 5-7200 seconds")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        res = poll_interactive_run(store, run_id)
        if res["status"] != "RUNNING":
            return res
        time.sleep(min(poll_interval, max(0.2, deadline - time.monotonic())))
    return {"status": "TIMEOUT", "run_id": run_id}


def cancel_interactive_run(store: Store, session: Optional[str] = None,
                           run_id: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_relay_config(store)
    if not cfg:
        return {"status": "NOT_CONFIGURED"}
    target_run = run_id
    if not target_run and session:
        row = store.db.execute("SELECT value FROM state WHERE key=?",
                               ("relay_active:" + session[:64],)).fetchone()
        if row:
            target_run = json.loads(row["value"]).get("run_id")
            with store.transaction():
                store.db.execute("DELETE FROM state WHERE key=?", ("relay_active:" + session[:64],))
    if not target_run:
        return {"status": "NO_ACTIVE_RUN"}
    env_id = cfg["environment_id"]
    flow_id = cfg["flow_id"]
    cancel_url = (f"https://{FLOW_API_HOST}/providers/Microsoft.ProcessSimple"
                  f"/environments/{env_id}/flows/{flow_id}/runs/{target_run}/cancel?api-version=2016-11-01")
    req = urllib.request.Request(
        cancel_url, data=b"",
        headers={"Authorization": "Bearer " + _cached_bearer_token(store)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = int(getattr(resp, "status", None) or resp.getcode())
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
    except OSError:
        code = 0
    return {"status": "CANCEL_REQUESTED", "run_id": target_run, "http_status": code}
