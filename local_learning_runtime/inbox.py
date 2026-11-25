"""Proactive Teams Workflows chat inbox: route plain mobile chat messages to live or new sessions."""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import RuntimeErrorBase, Store, canonical, digest, now, require
from .discovery import discover
from .gateway_transport import NoRedirect
from .learning import privacy_key, tokenize
from .lifecycle import parsed
from .notifier import (_deliver_sync, build_teams_payload, default_machine_name,
                       load_notifier_config, validate_webhook_url)
from .privacy import sanitize
from .relay import _cached_bearer_token, load_relay_config, send_interactive_relay


def inbox_config_path(store: Store) -> Path:
    return store.home / "inbox.json"


def load_inbox_config(store: Store) -> Optional[Dict[str, Any]]:
    path = inbox_config_path(store)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("reader_url"):
        return None
    return data


def configure_inbox(store: Store, approve: bool,
                    reader_url: Optional[str] = None,
                    enabled: Optional[bool] = None,
                    poll_seconds: Optional[int] = None,
                    allow_new_sessions: Optional[bool] = None,
                    new_session_client: Optional[str] = None,
                    default_cwd: Optional[str] = None,
                    machine_name: Optional[str] = None,
                    only_Target_tagged: Optional[bool] = None) -> Dict[str, Any]:
    require(approve, "Explicit approval required to configure proactive Teams inbox")
    existing = load_inbox_config(store) or {}
    final_url = (reader_url.strip() if isinstance(reader_url, str) and reader_url.strip()
                 else existing.get("reader_url"))
    require(bool(final_url), "Supply Workflows Chat Reader URL (--reader-stdin or LLR_TEAMS_READER_URL)")
    checked = validate_webhook_url(final_url)
    interval = poll_seconds if poll_seconds is not None else existing.get("poll_seconds", 8)
    require(type(interval) is int and 3 <= interval <= 3600, "poll_seconds must be 3-3600")
    runner_client = (new_session_client if new_session_client is not None
                     else existing.get("new_session_client", "copilot"))
    require(runner_client in ("copilot", "codex"), "new_session_client must be copilot or codex")
    relay_cfg = load_relay_config(store) or {}
    raw_machine = (machine_name.strip() if isinstance(machine_name, str) and machine_name.strip()
                   else existing.get("machine_name") or relay_cfg.get("machine_name") or default_machine_name())
    safe_machine = sanitize(raw_machine[:32], privacy_key(store))[0]
    fallback_cwd = default_cwd or existing.get("default_cwd") or ""
    if fallback_cwd:
        resolved = str(Path(fallback_cwd).expanduser().resolve())
        require(Path(resolved).is_dir(), "default_cwd must be an existing directory")
        fallback_cwd = resolved
    record = {
        "schema": "llr.inbox.v1",
        "enabled": bool(enabled if enabled is not None else existing.get("enabled", True)),
        "machine_name": safe_machine,
        "reader_url": checked["url"],
        "masked_url": checked["masked_url"],
        "poll_seconds": interval,
        "allow_new_sessions": bool(allow_new_sessions if allow_new_sessions is not None
                                   else existing.get("allow_new_sessions", True)),
        "new_session_client": runner_client,
        "default_cwd": fallback_cwd,
        "require_machine_tag": bool(only_Target_tagged if only_Target_tagged is not None
                                    else existing.get("require_machine_tag", False)),
        "updated_at": now(),
    }
    target = inbox_config_path(store)
    target.write_text(canonical(record), encoding="utf-8")
    target.chmod(0o600)
    return inbox_status(store)


def register_live_session(store: Store, session: str, client: str = "copilot",
                          cwd: str = "", title: str = "",
                          state: str = "idle") -> Dict[str, Any]:
    require(isinstance(session, str) and 1 <= len(session) <= 128, "Invalid session ID")
    key = privacy_key(store)
    safe_cwd = str(Path(cwd).expanduser().resolve()) if cwd and Path(cwd).expanduser().exists() else str(cwd)[:240]
    ws = None
    if safe_cwd and Path(safe_cwd).is_dir():
        found = discover(store, Path(safe_cwd), session[:64])
        ws = found.get("workspace")
    safe_title = sanitize(str(title)[:120], key)[0] if (
        title and not str(title).lstrip().startswith("<system_notification>")
    ) else ""
    ts = now()
    with store.transaction():
        old = store.db.execute("SELECT cwd,workspace,title,updated_at FROM live_sessions WHERE session=?",
                               (session,)).fetchone()
        final_cwd = safe_cwd or (old["cwd"] if old else "")
        final_ws = ws or (old["workspace"] if old else None)
        final_title = safe_title or (old["title"] if old else "")
        final_updated = ts if title else (old["updated_at"] if old else ts)
        store.db.execute(
            "INSERT INTO live_sessions VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET "
            "client=excluded.client,cwd=excluded.cwd,workspace=excluded.workspace,"
            "title=excluded.title,state=excluded.state,heartbeat_at=excluded.heartbeat_at,"
            "updated_at=excluded.updated_at",
            (session, str(client)[:24], final_cwd, final_ws, final_title, str(state)[:24], ts, final_updated),
        )
        store.db.execute(
            "INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("relay_session:" + session[:64], ts),
        )
    return {"session": session, "workspace": final_ws, "heartbeat_at": ts}


def active_live_sessions(store: Store, max_stale_seconds: int = 45) -> List[Dict[str, Any]]:
    clock = parsed(now())
    rows = store.db.execute(
        "SELECT * FROM live_sessions ORDER BY updated_at DESC, heartbeat_at DESC LIMIT 32"
    ).fetchall()
    alive: List[Dict[str, Any]] = []
    for row in rows:
        hb = parsed(row["heartbeat_at"])
        if clock and hb and (clock - hb).total_seconds() <= max_stale_seconds:
            alive.append(dict(row))
    return alive


def inbox_status(store: Store) -> Dict[str, Any]:
    cfg = load_inbox_config(store)
    cursor_row = store.db.execute("SELECT value FROM state WHERE key='inbox_last_msg_id'").fetchone()
    live = active_live_sessions(store)
    if not cfg:
        return {"configured": False, "enabled": False, "live_sessions": len(live)}
    checked = validate_webhook_url(cfg["reader_url"])
    return {
        "configured": True,
        "enabled": bool(cfg.get("enabled", True)),
        "machine_name": cfg.get("machine_name") or default_machine_name(),
        "masked_url": checked["masked_url"],
        "poll_seconds": int(cfg.get("poll_seconds", 8)),
        "allow_new_sessions": bool(cfg.get("allow_new_sessions", True)),
        "new_session_client": cfg.get("new_session_client", "copilot"),
        "default_cwd": cfg.get("default_cwd", ""),
        "require_machine_tag": bool(cfg.get("require_machine_tag", False)),
        "last_message_id": cursor_row["value"] if cursor_row else None,
        "live_sessions": [
            {"session": item["session"][:12], "client": item["client"],
             "workspace": item["workspace"], "cwd": item["cwd"], "title": item["title"]}
            for item in live
        ],
    }


def strip_teams_html(raw_html: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", raw_html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_proactive_directive(raw_text: str) -> Dict[str, Any]:
    """Parse optional @machine, #session, /new [workspace], or /sessions commands."""
    text = raw_text.strip()
    machine_tag = None
    session_prefix = None
    force_new = False
    new_target = None
    list_only = False

    while text:
        m_mac = re.match(r"^@([A-Za-z0-9_.-]{1,32})\s+(.*)$", text, re.S)
        if m_mac:
            machine_tag = m_mac.group(1)
            text = m_mac.group(2).strip()
            continue
        m_sess = re.match(r"^#([A-Za-z0-9_-]{4,64})\s+(.*)$", text, re.S)
        if m_sess:
            session_prefix = m_sess.group(1)
            text = m_sess.group(2).strip()
            continue
        break

    if re.fullmatch(r"/(sessions?|status|list)", text, re.I):
        list_only = True
    else:
        m_new = re.match(r"^/new(?:\s+@?([A-Za-z0-9_./-]+))?\s+(.+)$", text, re.S | re.I)
        if m_new:
            force_new = True
            new_target = m_new.group(1)
            text = m_new.group(2).strip()
    return {
        "machine_tag": machine_tag,
        "session_prefix": session_prefix,
        "force_new": force_new,
        "new_target": new_target,
        "list_only": list_only,
        "prompt": text,
    }


def _machine_matches(configured_name: str, tag: Optional[str], require_tag: bool) -> bool:
    if not tag:
        return not require_tag
    low_cfg = configured_name.lower()
    low_tag = tag.lower()
    return low_tag == "all" or low_cfg == low_tag or low_cfg.startswith(low_tag) or low_tag in low_cfg


def select_best_live_session(store: Store, prompt: str,
                             session_prefix: Optional[str] = None) -> Optional[Dict[str, Any]]:
    live = active_live_sessions(store)
    if not live:
        return None
    if session_prefix:
        low_pref = session_prefix.lower()
        for item in live:
            if item["session"].lower().startswith(low_pref):
                return item
        return None
    if len(live) == 1:
        return live[0]
    terms = tokenize(prompt[:1000])
    prompt_lower = prompt.lower()
    best_score = -10_000
    best_item = live[0]
    for idx, item in enumerate(live):
        score = 0
        ws = (item.get("workspace") or "").lower()
        cwd = (item.get("cwd") or "").lower()
        # Gather rich topic context from both current title and recent task_runs titles of this session!
        recent_titles = [
            r[0] for r in store.db.execute(
                "SELECT title FROM task_runs WHERE session=? ORDER BY updated_at DESC LIMIT 5",
                (item["session"],),
            ).fetchall() if r[0] and not str(r[0]).lstrip().startswith("<system_notification>")
        ]
        combined_topic = " ".join([item.get("title") or "", *recent_titles])
        if ws:
            for part in re.split(r"[^a-z0-9]+", ws):
                if len(part) >= 3 and part in prompt_lower:
                    score += 4
        if cwd:
            tail = Path(cwd).name.lower()
            if len(tail) >= 3 and tail in prompt_lower:
                score += 3
        if terms and combined_topic:
            score += 3 * len(terms & tokenize(combined_topic))
        # Prefer idle sessions (+150) over sessions currently busy in a long turn!
        idle_bonus = 150 if item.get("state") == "idle" else 0
        rank = score * 100 + idle_bonus - idx
        if rank > best_score:
            best_score = rank
            best_item = item
    return best_item


def _resolve_spawn_cwd(store: Store, cfg: Dict[str, Any],
                       target_hint: Optional[str], prompt: str) -> Path:
    if target_hint:
        low = target_hint.lower()
        for row in store.db.execute("SELECT id,root FROM workspaces"):
            if low in row["id"].lower() or low == Path(row["root"]).name.lower():
                if Path(row["root"]).is_dir():
                    return Path(row["root"])
    for row in store.db.execute("SELECT id,root FROM workspaces"):
        for slug in re.split(r"[^a-z0-9]+", row["id"].lower()):
            if len(slug) >= 4 and slug not in ("android", "generic") and slug in prompt.lower():
                if Path(row["root"]).is_dir():
                    return Path(row["root"])
    if cfg.get("default_cwd") and Path(cfg["default_cwd"]).is_dir():
        return Path(cfg["default_cwd"])
    first_ws = store.db.execute("SELECT root FROM workspaces ORDER BY id LIMIT 1").fetchone()
    if first_ws and Path(first_ws["root"]).is_dir():
        return Path(first_ws["root"])
    return Path.home()


def _send_reply_to_teams(store: Store, session_id: str, heading: str, summary: str) -> None:
    relay_cfg = load_relay_config(store)
    if relay_cfg and relay_cfg.get("enabled", True):
        try:
            send_interactive_relay(
                store, session_id, heading, summary,
                update_message="✅ 指令已送达电脑 Session！",
                turn_source="teams", force=True,
            )
            return
        except (RuntimeErrorBase, OSError, ValueError):
            pass
    notif_cfg = load_notifier_config(store)
    if notif_cfg and notif_cfg.get("enabled", True):
        try:
            card = build_teams_payload(store, "copilot", None, 0.0, "INFO", summary[:90], "proactive_reply")
            card["text"] = f"{heading}\n{summary[:800]}"
            _deliver_sync(store, card, timeout=8.0)
        except (RuntimeErrorBase, OSError, ValueError):
            pass


def claim_proactive_message(store: Store, session: str) -> Dict[str, Any]:
    with store.transaction():
        row = store.db.execute(
            "SELECT message_id,prompt FROM proactive_inbox "
            "WHERE target_session=? AND state='QUEUED' ORDER BY created_at ASC LIMIT 1",
            (session,),
        ).fetchone()
        if row is None:
            return {"status": "EMPTY"}
        store.db.execute(
            "UPDATE proactive_inbox SET state='DELIVERED',updated_at=? WHERE message_id=?",
            (now(), row["message_id"]),
        )
    return {"status": "CLAIMED", "message_id": row["message_id"], "prompt": row["prompt"]}


def _spawn_headless_worker(home: Path, message_id: str, cwd: Path,
                           client: str, prompt: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "local_learning_runtime.inbox", str(home)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, start_new_session=True,
    )
    if proc.stdin is not None:
        try:
            proc.stdin.write(canonical({
                "message_id": message_id, "cwd": str(cwd),
                "client": client, "prompt": prompt,
            }).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass


def fetch_chat_messages(store: Store, reader_url: str, top: int = 5) -> List[Dict[str, Any]]:
    checked = validate_webhook_url(reader_url)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    parsed_url = urllib.parse.urlparse(checked["url"])
    if parsed_url.scheme == "https" and "sig=" not in parsed_url.query:
        headers["Authorization"] = "Bearer " + _cached_bearer_token(store)
    req = urllib.request.Request(
        checked["url"], data=canonical({"top": top}).encode("utf-8"),
        headers=headers, method="POST",
    )
    with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as resp:
        data = json.loads(resp.read(256_000).decode("utf-8"))
    return data if isinstance(data, list) else data.get("value", [])


def poll_proactive_inbox(store: Store, messages_override: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    cfg = load_inbox_config(store)
    if not cfg or not cfg.get("enabled", True):
        return {"status": "DISABLED", "processed": []}
    messages = (messages_override if messages_override is not None
                else fetch_chat_messages(store, cfg["reader_url"], top=5))
    # Sort chronologically by numeric Teams message ID (or string fallback)
    def _msg_sort_key(item: Dict[str, Any]):
        mid = str(item.get("id", ""))
        return (0, int(mid)) if mid.isdigit() else (1, mid)

    ordered = sorted([m for m in messages if isinstance(m, dict) and m.get("id")], key=_msg_sort_key)
    cursor_row = store.db.execute("SELECT value FROM state WHERE key='inbox_last_msg_id'").fetchone()
    if cursor_row is None:
        # First poll initializes high-water mark so historical chat messages are never replayed!
        newest_id = str(ordered[-1]["id"]) if ordered else "0"
        with store.transaction():
            store.db.execute("INSERT OR REPLACE INTO state VALUES ('inbox_last_msg_id', ?)", (newest_id,))
        return {"status": "INITIALIZED_CURSOR", "last_message_id": newest_id, "processed": []}

    last_id = cursor_row["value"]
    machine_label = cfg.get("machine_name") or default_machine_name()
    processed: List[Dict[str, Any]] = []

    for msg in ordered:
        mid = str(msg["id"])
        if mid.isdigit() and last_id.isdigit() and int(mid) <= int(last_id):
            continue
        if mid == last_id or store.db.execute(
                "SELECT 1 FROM proactive_inbox WHERE message_id=?", (mid,)).fetchone():
            continue
        # Advance cursor immediately so neither bot messages nor errors loop.
        with store.transaction():
            store.db.execute("INSERT OR REPLACE INTO state VALUES ('inbox_last_msg_id', ?)", (mid,))
        last_id = mid

        if msg.get("messageType", "message") != "message":
            continue
        sender = msg.get("from") or {}
        if sender.get("application") or not isinstance(sender.get("user"), dict):
            # Ignore messages sent by the Workflows bot itself!
            continue
        raw_text = strip_teams_html(str((msg.get("body") or {}).get("content", "")))
        if not raw_text:
            continue
        parsed_cmd = parse_proactive_directive(raw_text)
        if not _machine_matches(machine_label, parsed_cmd["machine_tag"],
                                bool(cfg.get("require_machine_tag", False))):
            processed.append({"message_id": mid, "action": "IGNORED_OTHER_MACHINE"})
            continue

        if parsed_cmd["list_only"]:
            live = active_live_sessions(store)
            if not live:
                summary = "当前这台电脑没有活跃打开的 VS Code Session；直接发消息或 `/new <指令>` 可自动启动新无头会话。"
            else:
                lines = [
                    f"• `#{item['session'][:8]}` ({item['client']}) — {item['workspace'] or item['cwd']} | {item['title'] or 'idle'}"
                    for item in live[:6]
                ]
                summary = "当前活跃 Session（可用 `#前8位ID 指令` 指定）：\n" + "\n".join(lines)
            _send_reply_to_teams(store, "session-router", "📋 [Session 路由列表]", summary)
            processed.append({"message_id": mid, "action": "LISTED_SESSIONS"})
            continue

        prompt_text = parsed_cmd["prompt"]
        if not prompt_text:
            continue

        chosen = (None if parsed_cmd["force_new"]
                  else select_best_live_session(store, prompt_text, parsed_cmd["session_prefix"]))
        if chosen is not None:
            safe_prompt = prompt_text[:4000]
            with store.transaction():
                store.db.execute(
                    "INSERT OR IGNORE INTO proactive_inbox VALUES (?,?,?,?,?,?)",
                    (mid, chosen["session"], safe_prompt, "QUEUED", now(), now()),
                )
            busy_note = "（该 Session 当前正忙，将在本轮结束后紧接着执行）" if chosen.get("state") == "active" else "（该 Session 当前空闲，正在立即执行）"
            notif_cfg = load_notifier_config(store)
            if notif_cfg and notif_cfg.get("enabled", True):
                try:
                    ack = build_teams_payload(
                        store, chosen.get("client", "copilot"),
                        chosen.get("workspace"), 0.0, "ROUTED",
                        safe_prompt[:90], "proactive_ack",
                    )
                    ack["text"] = f"📨 [{machine_label}] 已路由至 Session `#{chosen['session'][:8]}` {busy_note}：{safe_prompt[:80]}"
                    _deliver_sync(store, ack, timeout=6.0)
                except (RuntimeErrorBase, OSError, ValueError):
                    pass
            processed.append({
                "message_id": mid,
                "action": "QUEUED_TO_LIVE_SESSION",
                "session": chosen["session"],
            })
        elif cfg.get("allow_new_sessions", True):
            spawn_cwd = _resolve_spawn_cwd(store, cfg, parsed_cmd["new_target"], prompt_text)
            headless_id = "headless-" + mid[-10:]
            with store.transaction():
                store.db.execute(
                    "INSERT OR IGNORE INTO proactive_inbox VALUES (?,?,?,?,?,?)",
                    (mid, headless_id, sanitize(prompt_text[:200], privacy_key(store))[0],
                     "SPAWNED_NEW", now(), now()),
                )
            _spawn_headless_worker(
                store.home, mid, spawn_cwd,
                cfg.get("new_session_client", "copilot"), prompt_text[:4000],
            )
            processed.append({
                "message_id": mid,
                "action": "SPAWNED_NEW_SESSION",
                "cwd": str(spawn_cwd),
                "client": cfg.get("new_session_client", "copilot"),
            })
        else:
            _send_reply_to_teams(
                store, "no-session", "⚠️ [无活跃 Session]",
                "当前没有打开的 VS Code Session，且新会话自动拉起已关闭。",
            )
            processed.append({"message_id": mid, "action": "NO_SESSION"})

    return {"status": "OK", "last_message_id": last_id, "processed": processed}


def _run_headless_new_session(home: Path, spec: Dict[str, Any]) -> None:
    cwd = Path(spec["cwd"])
    prompt = str(spec["prompt"])
    client = spec.get("client", "copilot")
    session_id = "new-" + str(spec["message_id"])[-10:]
    output_text = ""
    if client == "codex":
        codex_bin = shutil.which("codex") or "/opt/homebrew/bin/codex"
        if os.path.isfile(codex_bin):
            res = subprocess.run(
                [codex_bin, "exec", "-s", "read-only", prompt],
                cwd=str(cwd), capture_output=True, text=True, timeout=300,
            )
            output_text = (res.stdout.strip() or res.stderr.strip())[:1200]
        else:
            output_text = "Codex CLI not found on this Mac."
    else:
        copilot_bin = shutil.which("copilot") or "/opt/homebrew/bin/copilot"
        if os.path.isfile(copilot_bin):
            res = subprocess.run(
                [copilot_bin, "-s", "-p", prompt,
                 "--allow-tool", "view", "--allow-tool", "grep", "--allow-tool", "glob",
                 "--allow-tool", "local_learning"],
                cwd=str(cwd), capture_output=True, text=True, timeout=300,
            )
            output_text = (res.stdout.strip() or res.stderr.strip())[:1200]
        else:
            output_text = "Copilot CLI not found on this Mac."
    store = Store(home)
    try:
        with store.transaction():
            store.db.execute(
                "UPDATE proactive_inbox SET state='COMPLETED_NEW',updated_at=? WHERE message_id=?",
                (now(), str(spec["message_id"])),
            )
        tail = "/".join(cwd.parts[-2:])
        _send_reply_to_teams(
            store, session_id,
            f"🆕 [新建 {client.capitalize()} 会话完成] · {tail}",
            output_text or "新会话已执行完毕（无文本输出）。",
        )
    finally:
        store.close()


def main() -> None:
    require(len(sys.argv) == 2, "Expected state home path")
    home = Path(sys.argv[1]).expanduser().resolve()
    raw = sys.stdin.buffer.read(64_001)
    require(len(raw) <= 64_000, "Oversized headless task spec")
    _run_headless_new_session(home, json.loads(raw))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        raise SystemExit(1)
