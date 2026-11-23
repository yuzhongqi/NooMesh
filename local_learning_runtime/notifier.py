"""Privacy-safe Teams Workflows webhook notifier for turn and mission completion."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .core import RuntimeErrorBase, Store, canonical, digest, now, require
from .gateway_transport import NoRedirect
from .learning import privacy_key
from .lifecycle import parsed
from .privacy import sanitize

ALLOWED_WEBHOOK_SUFFIXES = (
    ".logic.azure.com",
    ".api.powerplatform.com",
    ".powerautomate.com",
    ".webhook.office.com",
)


def validate_webhook_url(url: str) -> Dict[str, str]:
    require(isinstance(url, str) and 16 <= len(url.strip()) <= 4000,
            "Webhook URL must be a bounded string")
    cleaned = url.strip()
    parsed_url = urllib.parse.urlparse(cleaned)
    host = (parsed_url.hostname or "").lower()
    require(bool(host) and not parsed_url.username and not parsed_url.password,
            "Webhook URL cannot embed credentials")
    if parsed_url.scheme == "http":
        require(host in ("127.0.0.1", "localhost"),
                "Plain HTTP webhook is restricted to localhost/127.0.0.1 testing")
    else:
        require(parsed_url.scheme == "https", "Teams Workflows webhook must use HTTPS")
        require(any(host.endswith(suffix) for suffix in ALLOWED_WEBHOOK_SUFFIXES),
                "Webhook host must be an official Microsoft Workflows/PowerPlatform/Office domain")
    masked = f"{parsed_url.scheme}://{host}{parsed_url.path[:32]}...[REDACTED]"
    return {"url": cleaned, "host": host, "masked_url": masked}


def config_path(store: Store) -> Path:
    return store.home / "notifier.json"


def load_notifier_config(store: Store) -> Optional[Dict[str, Any]]:
    path = config_path(store)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("webhook_url"):
        return None
    return data


def configure_notifier(store: Store, approve: bool,
                       webhook_url: Optional[str] = None,
                       min_duration_seconds: Optional[int] = None,
                       enabled: Optional[bool] = None,
                       include_unregistered: Optional[bool] = None) -> Dict[str, Any]:
    require(approve, "Explicit approval required to configure external webhook notifications")
    existing = load_notifier_config(store) or {}
    final_url = webhook_url.strip() if isinstance(webhook_url, str) and webhook_url.strip() else existing.get("webhook_url")
    require(bool(final_url), "Supply a Teams Workflows webhook URL (--webhook-file, --webhook-stdin, or LLR_TEAMS_WEBHOOK_URL)")
    checked = validate_webhook_url(final_url)
    duration = (min_duration_seconds if min_duration_seconds is not None
                else existing.get("min_duration_seconds", 30))
    require(type(duration) is int and 0 <= duration <= 86400,
            "min_duration_seconds must be 0-86400")
    is_enabled = enabled if enabled is not None else existing.get("enabled", True)
    unregistered = (include_unregistered if include_unregistered is not None
                    else existing.get("include_unregistered", True))
    record = {
        "schema": "llr.notifier.v1",
        "kind": "teams_workflows",
        "enabled": bool(is_enabled),
        "webhook_url": checked["url"],
        "host": checked["host"],
        "masked_url": checked["masked_url"],
        "min_duration_seconds": duration,
        "include_unregistered": bool(unregistered),
        "updated_at": now(),
    }
    target = config_path(store)
    target.write_text(canonical(record), encoding="utf-8")
    target.chmod(0o600)
    return notifier_status(store)


def notifier_status(store: Store) -> Dict[str, Any]:
    cfg = load_notifier_config(store)
    last = store.db.execute(
        "SELECT event,body,created_at FROM client_receipts WHERE client='teams-webhook' "
        "ORDER BY created_at DESC LIMIT 1").fetchone()
    last_delivery = None
    if last is not None:
        parsed_body = json.loads(last["body"])
        last_delivery = {
            "event": last["event"],
            "created_at": last["created_at"],
            "http_status": parsed_body.get("http_status"),
            "workspace": parsed_body.get("workspace"),
        }
    if not cfg:
        return {"configured": False, "enabled": False, "last_delivery": last_delivery}
    return {
        "configured": True,
        "kind": cfg.get("kind", "teams_workflows"),
        "enabled": bool(cfg.get("enabled", False)),
        "host": cfg.get("host"),
        "masked_url": cfg.get("masked_url"),
        "min_duration_seconds": cfg.get("min_duration_seconds", 30),
        "include_unregistered": bool(cfg.get("include_unregistered", True)),
        "updated_at": cfg.get("updated_at"),
        "last_delivery": last_delivery,
    }


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    if total < 60:
        return f"{total}s"
    minutes, rem = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def build_teams_payload(store: Store, client: str, workspace: Optional[str],
                        duration_seconds: float, task_state: str,
                        title: str, event_kind: str = "turn_completed") -> Dict[str, Any]:
    key = privacy_key(store)
    safe_ws = sanitize((workspace or "unregistered-cwd")[:80], key)[0]
    safe_title = sanitize((title or "Coding session task")[:90], key)[0]
    safe_state = sanitize(str(task_state)[:40], key)[0]
    client_label = {
        "copilot": "VS Code Copilot",
        "vscode-local": "VS Code",
        "codex": "Codex",
        "codex-hooks": "Codex",
        "mission": "LLR Mission",
        "manual-test": "LLR Notify Test",
    }.get(client, str(client)[:32])
    duration_text = format_duration(duration_seconds)
    heading = f"[{client_label}] Task finished ({duration_text}) · {safe_ws}"
    facts = [
        {"title": "Client", "value": client_label},
        {"title": "Workspace", "value": safe_ws},
        {"title": "Duration", "value": duration_text},
        {"title": "State", "value": safe_state},
    ]
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": heading, "weight": "Bolder",
             "size": "Medium", "wrap": True},
            {"type": "FactSet", "facts": facts},
            {"type": "TextBlock", "text": safe_title, "wrap": True, "isSubtle": True},
        ],
    }
    return {
        "type": "message",
        "text": f"{heading} — {safe_title}",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": card,
        }],
        "llr": {
            "schema": "llr.notification.v1",
            "event": event_kind,
            "client": client_label,
            "workspace": safe_ws,
            "duration_seconds": round(duration_seconds, 1),
            "duration_text": duration_text,
            "state": safe_state,
            "summary": safe_title,
        },
    }


def _deliver_sync(store: Store, payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    cfg = load_notifier_config(store)
    require(cfg is not None and cfg.get("enabled") is True, "Teams notifier is not enabled")
    checked = validate_webhook_url(cfg["webhook_url"])
    raw = canonical(payload).encode("utf-8")
    require(len(raw) <= 16_000, "Notification payload exceeds 16000 bytes")
    req = urllib.request.Request(
        checked["url"], data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    http_status = 0
    ok = False
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as resp:
            http_status = int(getattr(resp, "status", resp.getcode()))
            resp.read(4096)
        ok = 200 <= http_status < 300
    except urllib.error.HTTPError as exc:
        http_status = int(exc.code)
        ok = False
    except (OSError, RuntimeErrorBase, ValueError):
        http_status = 0
        ok = False
    ws = payload.get("llr", {}).get("workspace")
    ws_ref = ws if ws and store.db.execute("SELECT 1 FROM workspaces WHERE id=?", (ws,)).fetchone() else None
    receipt = {
        "event": "notificationSent" if ok else "notificationFailed",
        "http_status": http_status,
        "workspace": ws_ref,
        "duration_seconds": payload.get("llr", {}).get("duration_seconds"),
    }
    ts = now()
    with store.transaction():
        store.db.execute(
            "INSERT OR IGNORE INTO client_receipts VALUES (?,?,?,?,?,?,?)",
            (digest(canonical(["teams-webhook", ts, receipt]).encode()),
             "teams-webhook", "teams-webhook", receipt["event"], ws_ref,
             canonical(receipt), ts))
    require(ok, f"Teams webhook delivery failed (HTTP {http_status})")
    return {"status": "SENT", "http_status": http_status, "masked_url": checked["masked_url"]}


def dispatch_background(home: Path, payload: Dict[str, Any]) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "local_learning_runtime.notifier", str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True,
    )
    if proc.stdin is not None:
        try:
            proc.stdin.write(canonical(payload).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass


def maybe_notify_turn_end(store: Store, client: str, session: str,
                          workspace: Optional[str], sync: bool = False) -> Dict[str, Any]:
    cfg = load_notifier_config(store)
    if not cfg or not cfg.get("enabled", False):
        return {"status": "DISABLED"}
    if not workspace and not cfg.get("include_unregistered", True):
        return {"status": "SKIPPED_UNREGISTERED"}
    start_row = store.db.execute(
        "SELECT id,body,created_at FROM client_receipts WHERE session=? "
        "AND event IN ('userPromptSubmitted','sessionStart') "
        "ORDER BY created_at DESC LIMIT 1", (session,)).fetchone()
    clock = parsed(now())
    started_at = parsed(start_row["created_at"]) if start_row else None
    duration = max(0.0, (clock - started_at).total_seconds()) if (clock and started_at) else 0.0
    start_meta = json.loads(start_row["body"]) if start_row else {}
    if start_meta.get("gated") in ("alias_irrelevant", "unknown_workspace", "ambiguous"):
        workspace = None
        if not cfg.get("include_unregistered", True):
            return {"status": "SKIPPED_UNREGISTERED"}
    task_row = store.db.execute(
        "SELECT title,state,updated_at FROM task_runs WHERE session=? ORDER BY updated_at DESC LIMIT 1",
        (session,)).fetchone()
    task_updated = parsed(task_row["updated_at"]) if task_row else None
    current_task = task_row if (task_row and (not started_at or (task_updated and task_updated >= started_at))) else None
    title = (current_task["title"] if current_task
             else start_meta.get("prompt_summary") or f"Session {session[:12]}")
    state = current_task["state"] if current_task else "COMPLETED"
    min_dur = int(cfg.get("min_duration_seconds", 30))
    if duration < min_dur and state != "FAIL":
        return {"status": "SKIPPED_SHORT_TURN", "duration_seconds": round(duration, 1)}
    dedup_key = "notified:" + (start_row["id"] if start_row else digest(
        canonical([session, now()[:16]]).encode()))
    with store.transaction():
        if store.db.execute("SELECT 1 FROM state WHERE key=?", (dedup_key,)).fetchone():
            return {"status": "ALREADY_NOTIFIED"}
        store.db.execute("INSERT INTO state VALUES (?,?)", (dedup_key, now()))
        store.db.execute(
            "DELETE FROM state WHERE key LIKE 'notified:%' AND key NOT IN "
            "(SELECT key FROM state WHERE key LIKE 'notified:%' ORDER BY value DESC LIMIT 256)")
    payload = build_teams_payload(store, client, workspace, duration, state, title, "turn_completed")
    if sync:
        return _deliver_sync(store, payload)
    dispatch_background(store.home, payload)
    return {"status": "DISPATCHED", "duration_seconds": round(duration, 1)}


def maybe_notify_mission(store: Store, mission_id: str, sync: bool = False) -> Dict[str, Any]:
    cfg = load_notifier_config(store)
    if not cfg or not cfg.get("enabled", False):
        return {"status": "DISABLED"}
    row = store.db.execute("SELECT workspace,state,body FROM missions WHERE id=?",
                           (mission_id,)).fetchone()
    if row is None or row["state"] not in ("COMPLETED", "PARTIAL", "BUDGET_EXHAUSTED", "WAITING_USER"):
        return {"status": "NOT_TERMINAL"}
    spec = json.loads(row["body"])
    dedup_key = "notified:mission:" + digest(canonical([mission_id, row["state"]]).encode())
    with store.transaction():
        if store.db.execute("SELECT 1 FROM state WHERE key=?", (dedup_key,)).fetchone():
            return {"status": "ALREADY_NOTIFIED"}
        store.db.execute("INSERT INTO state VALUES (?,?)", (dedup_key, now()))
    payload = build_teams_payload(
        store, "mission", row["workspace"], float(spec.get("active_seconds", 0.0)),
        row["state"], spec.get("goal", mission_id), "mission_" + row["state"].lower())
    if sync:
        return _deliver_sync(store, payload)
    dispatch_background(store.home, payload)
    return {"status": "DISPATCHED", "state": row["state"]}


def send_test_notification(store: Store, approve: bool) -> Dict[str, Any]:
    require(approve, "Explicit approval required to send a live test notification")
    payload = build_teams_payload(
        store, "manual-test", None, 42.0, "READY",
        "Clone Yourself Teams Workflows notification is connected.", "test_ping")
    return _deliver_sync(store, payload, timeout=8.0)


def main() -> None:
    require(len(sys.argv) == 2, "Expected state home path")
    home = Path(sys.argv[1]).expanduser().resolve()
    raw = sys.stdin.buffer.read(32_001)
    require(len(raw) <= 32_000, "Oversized notification payload")
    payload = json.loads(raw)
    store = Store(home)
    try:
        _deliver_sync(store, payload, timeout=5.0)
    finally:
        store.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        raise SystemExit(1)
