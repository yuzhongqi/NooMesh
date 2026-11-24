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
from .notifier import (_acquire_flow_bearer_token, default_machine_name,
                       detect_user_presence, format_duration, validate_webhook_url)
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
                    poll_timeout_seconds: Optional[int] = None,
                    away_only: Optional[bool] = None,
                    away_idle_seconds: Optional[int] = None,
                    machine_name: Optional[str] = None) -> Dict[str, Any]:
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
    idle_sec = (away_idle_seconds if away_idle_seconds is not None
                else existing.get("away_idle_seconds", 60))
    require(type(idle_sec) is int and 5 <= idle_sec <= 86400,
            "away_idle_seconds must be 5-86400")
    raw_machine = (machine_name.strip() if isinstance(machine_name, str) and machine_name.strip()
                   else existing.get("machine_name") or default_machine_name())
    safe_machine = sanitize(raw_machine[:32], privacy_key(store))[0]
    require(bool(safe_machine), "machine_name must be nonempty")
    record = {
        "schema": "llr.relay.v1",
        "enabled": bool(enabled if enabled is not None else existing.get("enabled", True)),
        "machine_name": safe_machine,
        "auto_on_stop": bool(auto_on_stop if auto_on_stop is not None
                             else existing.get("auto_on_stop", True)),
        "away_only": bool(away_only if away_only is not None else existing.get("away_only", False)),
        "away_idle_seconds": idle_sec,
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
        return {"configured": False, "enabled": False, "machine_name": default_machine_name()}
    checked = validate_webhook_url(cfg["webhook_url"])
    return {
        "configured": True,
        "enabled": bool(cfg.get("enabled", True)),
        "machine_name": cfg.get("machine_name") or default_machine_name(),
        "auto_on_stop": bool(cfg.get("auto_on_stop", True)),
        "away_only": bool(cfg.get("away_only", False)),
        "away_idle_seconds": int(cfg.get("away_idle_seconds", 60)),
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
    cfg = load_relay_config(store) or {}
    machine_label = sanitize((cfg.get("machine_name") or default_machine_name())[:32], key)[0]
    req_id = request_id or ("req-" + uuid.uuid4().hex[:12])
    raw_heading = sanitize((heading or "Coding Session")[:140], key)[0]
    if f"[{machine_label}" not in raw_heading and f"· {machine_label}]" not in raw_heading:
        if "[" in raw_heading:
            safe_heading = raw_heading.replace("[", f"[{machine_label} · ", 1)
        else:
            safe_heading = f"[{machine_label}] {raw_heading}"
    else:
        safe_heading = raw_heading
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
                "text": f"Machine: {machine_label} · Session: {session[:12]} · Request: {req_id}",
                "isSubtle": True,
                "size": "Small",
                "wrap": True,
            },
        ],
        "actions": [{
            "type": "Action.Submit",
            "title": f"🚀 发回 {machine_label} 当前 Session",
            "data": {"request_id": req_id, "session": session[:36], "machine": machine_label},
        }],
    }
    return {"request_id": req_id, "machine_name": machine_label, "card": card}


def send_interactive_relay(store: Store, session: str, heading: str,
                           summary: str, choices: Optional[List[Dict[str, str]]] = None,
                           update_message: str = "✅ 已发回电脑当前 Session！",
                           turn_source: str = "local",
                           elapsed_seconds: Optional[float] = None,
                           force: bool = False) -> Dict[str, Any]:
    cfg = load_relay_config(store)
    require(cfg is not None and cfg.get("enabled", True), "Interactive Teams relay is not enabled")
    require(turn_source in ("local", "teams"), "turn_source must be local or teams")
    if not force and turn_source == "local":
        presence = None
        if cfg.get("away_only", False):
            presence = detect_user_presence(int(cfg.get("away_idle_seconds", 60)))
            if not presence["user_away"]:
                return {"status": "SKIPPED_USER_AT_COMPUTER", "presence": presence}
        screen_locked = bool(presence and presence.get("screen_locked"))
        if elapsed_seconds is not None and not screen_locked:
            if float(elapsed_seconds) < int(cfg.get("min_duration_seconds", 15)):
                return {"status": "SKIPPED_SHORT_TURN",
                        "elapsed_seconds": round(float(elapsed_seconds), 1)}
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


def _default_flow_api_root(cfg: Dict[str, Any]) -> str:
    parsed_wh = urllib.parse.urlparse(cfg.get("webhook_url", ""))
    if parsed_wh.scheme == "http" and parsed_wh.hostname in ("127.0.0.1", "localhost") and parsed_wh.port:
        return f"http://{parsed_wh.hostname}:{parsed_wh.port}"
    return f"https://{FLOW_API_HOST}/providers/Microsoft.ProcessSimple"


def poll_interactive_run(store: Store, run_id: str,
                         base_api_url: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_relay_config(store)
    require(cfg is not None, "Interactive Teams relay is not configured")
    require(isinstance(run_id, str) and 4 <= len(run_id) <= 128 and "/" not in run_id,
            "Invalid workflow run_id")
    env_id = cfg["environment_id"]
    flow_id = cfg["flow_id"]
    api_root = base_api_url or _default_flow_api_root(cfg)
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
    api_root = _default_flow_api_root(cfg)
    cancel_url = f"{api_root}/environments/{env_id}/flows/{flow_id}/runs/{target_run}/cancel?api-version=2016-11-01"
    cancel_headers = {}
    if urllib.parse.urlparse(cancel_url).hostname == FLOW_API_HOST:
        cancel_headers["Authorization"] = "Bearer " + _cached_bearer_token(store)
    req = urllib.request.Request(
        cancel_url, data=b"",
        headers=cancel_headers,
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


def set_codex_remote_turn(store: Store, session: str, active: bool) -> Dict[str, Any]:
    key = "codex_remote_turn:" + session[:64]
    with store.transaction():
        if active:
            store.db.execute(
                "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, now()))
        else:
            store.db.execute("DELETE FROM state WHERE key=?", (key,))
    return {"session": session, "codex_remote_turn": active}


def maybe_start_codex_relay(store: Store, session: str, workspace: Optional[str],
                            payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cfg = load_relay_config(store)
    if not cfg or not cfg.get("enabled", True) or not cfg.get("auto_on_stop", True):
        return None
    with store.transaction():
        store.db.execute(
            "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("relay_session:" + session[:64], now()))
    remote_flag = bool(payload.get("stop_hook_active")) or bool(
        store.db.execute("SELECT 1 FROM state WHERE key=?",
                         ("codex_remote_turn:" + session[:64],)).fetchone()
    )
    turn_source = "teams" if remote_flag else "local"
    start_row = store.db.execute(
        "SELECT created_at,body FROM client_receipts WHERE session=? "
        "AND event IN ('userPromptSubmitted','sessionStart','agentStop') "
        "ORDER BY created_at DESC LIMIT 1", (session,)).fetchone()
    clock = parsed(now())
    started_at = parsed(start_row["created_at"]) if start_row else None
    duration = max(0.0, (clock - started_at).total_seconds()) if (clock and started_at) else 0.0
    start_meta = json.loads(start_row["body"]) if start_row else {}
    task_row = store.db.execute(
        "SELECT title FROM task_runs WHERE session=? ORDER BY updated_at DESC LIMIT 1",
        (session,)).fetchone()
    assistant_text = (payload.get("last_assistant_message")
                      or payload.get("response")
                      or payload.get("finalMessage") or "")
    fallback_title = (task_row["title"] if task_row else start_meta.get("prompt_summary") or "")
    summary = (str(assistant_text).strip()
               or (f"已完成：{fallback_title}\n可在下方输入指令继续当前 Codex Session。"
                   if fallback_title else "Codex 当前轮次已执行完毕，可在下方输入指令继续执行。"))
    cwd = str(payload.get("cwd") or "")
    cwd_tail = "/".join(Path(cwd).parts[-2:]) if cwd else (workspace or "workspace")
    heading = f"✅ [Codex] 任务阶段完成 ({format_duration(duration)}) · {workspace or cwd_tail}"
    sent = send_interactive_relay(
        store, session, heading, summary,
        update_message="✅ 指令已送达当前 Codex Session，正在继续执行！",
        turn_source=turn_source, elapsed_seconds=duration, force=False,
    )
    if sent.get("status") != "WAITING":
        if sent.get("status") == "SKIPPED_USER_AT_COMPUTER":
            set_codex_remote_turn(store, session, False)
        return {"skipped": sent.get("status")}
    return {
        "run_id": sent["run_id"],
        "session": session,
        "timeout_seconds": int(cfg.get("poll_timeout_seconds", 1800)),
    }

