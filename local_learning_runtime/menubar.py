"""macOS Menu Bar status item & live local-knowledge dashboard manager."""

import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core import RuntimeErrorBase, Store, canonical, file_digest, now, require, require_private_location
from .inbox import load_inbox_config
from .knowledge import export_portal
from .lifecycle import parsed
from .notifier import default_machine_name, load_notifier_config
from .relay import load_relay_config

LAUNCH_AGENT_LABEL = "com.cloneyourself.menubar"


def _lock_held(lock_file: Path) -> bool:
    if os.name != "posix" or not lock_file.is_file():
        return False
    import fcntl
    with lock_file.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False


def _read_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.is_file():
        return None
    try:
        val = int(pid_file.read_text(encoding="utf-8").strip())
        return val if val > 1 else None
    except (OSError, ValueError):
        return None


def probe_host_running(home: Path) -> Dict[str, Any]:
    home = home.expanduser().resolve()
    online = _lock_held(home / "host-owner.lock")
    pid = _read_pid(home / "host.pid") if online else None
    return {"online": online, "pid": pid}


def probe_menubar_running(home: Path) -> Dict[str, Any]:
    home = home.expanduser().resolve()
    running = _lock_held(home / "menubar.lock")
    pid = _read_pid(home / "menubar.pid") if running else None
    plist_path = Path.home() / "Library" / "LaunchAgents" / (LAUNCH_AGENT_LABEL + ".plist")
    return {
        "running": running,
        "pid": pid,
        "login_agent_installed": plist_path.is_file(),
        "login_agent_path": str(plist_path) if plist_path.is_file() else None,
    }


def relative_time_zh(iso_value: Optional[str], clock: datetime) -> str:
    ts = parsed(iso_value)
    if ts is None:
        return ""
    sec = max(0.0, (clock - ts).total_seconds())
    if sec < 45:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)}分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)}小时前"
    days = int(sec // 86400)
    if days == 1:
        return "昨天"
    return f"{days}天前"


def _clean_task_title(raw_title: str) -> str:
    if not raw_title:
        return ""
    text = str(raw_title).strip()
    if text.startswith("<system_notification>"):
        return ""
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return first_line[:96]


def _knowledge_title_catalog(store: Store) -> Dict[Tuple[str, str], Dict[str, str]]:
    lookup: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in store.db.execute("SELECT id,body FROM claims"):
        try:
            body = json.loads(row["body"])
            spec = body["spec"]
            title = spec.get("provenance", {}).get("title") or spec["statement"]
            lookup[("claim", row["id"])] = {
                "title": str(title)[:96],
                "workspace": spec["workspace"],
                "lifecycle": body.get("lifecycle", "active"),
            }
        except (KeyError, ValueError):
            continue
    for row in store.db.execute("SELECT workspace,id,lifecycle,body FROM memos"):
        try:
            spec = json.loads(row["body"])
            lookup[("memo", row["id"])] = {
                "title": str(spec.get("title", row["id"]))[:96],
                "workspace": row["workspace"],
                "lifecycle": row["lifecycle"],
                "memo_kind": spec.get("kind", "rule"),
            }
        except ValueError:
            continue
    for row in store.db.execute("SELECT workspace,id,lifecycle,body FROM solutions"):
        try:
            spec = json.loads(row["body"])
            lookup[("solution", row["id"])] = {
                "title": str(spec.get("problem", {}).get("goal", row["id"]))[:96],
                "workspace": row["workspace"],
                "lifecycle": row["lifecycle"],
            }
        except ValueError:
            continue
    return lookup


def _select_primary_workspace(store: Store) -> Optional[str]:
    live = store.db.execute(
        "SELECT workspace FROM live_sessions WHERE workspace IS NOT NULL "
        "ORDER BY heartbeat_at DESC LIMIT 1"
    ).fetchone()
    if live and live["workspace"]:
        return live["workspace"]
    recent_ev = store.db.execute(
        "SELECT workspace FROM memory_events ORDER BY occurred_at DESC LIMIT 1"
    ).fetchone()
    if recent_ev and recent_ev["workspace"]:
        return recent_ev["workspace"]
    for row in store.db.execute("SELECT id,root FROM workspaces ORDER BY id"):
        if Path(row["root"]).is_dir():
            return row["id"]
    first = store.db.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1").fetchone()
    return first["id"] if first else None


def menubar_snapshot(store: Store) -> Dict[str, Any]:
    clock = datetime.now(timezone.utc)
    day_ago = (clock - timedelta(hours=24)).isoformat()
    host_info = probe_host_running(store.home)
    policy_path = store.home / "automation.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    relay_cfg = load_relay_config(store) or {}
    notif_cfg = load_notifier_config(store) or {}
    inbox_cfg = load_inbox_config(store) or {}
    machine_name = (
        relay_cfg.get("machine_name")
        or inbox_cfg.get("machine_name")
        or notif_cfg.get("machine_name")
        or default_machine_name()
    )
    primary_ws = _select_primary_workspace(store)
    lookup = _knowledge_title_catalog(store)

    active_claims = sum(
        1 for (kind, _), meta in lookup.items()
        if kind == "claim" and meta.get("lifecycle") == "active"
    )
    active_memos = sum(
        1 for (kind, _), meta in lookup.items()
        if kind == "memo" and meta.get("lifecycle") == "active"
    )
    verified_solutions = sum(
        1 for (kind, _), meta in lookup.items()
        if kind == "solution" and meta.get("lifecycle") == "verified"
    )
    custom_themes = store.db.execute("SELECT count(*) FROM themes").fetchone()[0]
    hit_events = ("retrieved", "expanded", "selected", "executed")
    today_hits = store.db.execute(
        "SELECT count(*) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND occurred_at >= ?",
        (day_ago,),
    ).fetchone()[0]
    total_hits = store.db.execute(
        "SELECT count(*) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed')"
    ).fetchone()[0]

    # Map session -> latest human task title (prefer descriptive titles over 1-word replies)
    session_titles: Dict[str, str] = {}
    fallback_short_titles: Dict[str, str] = {}
    for row in store.db.execute(
        "SELECT session,title FROM task_runs ORDER BY updated_at DESC LIMIT 200"
    ):
        sid = row["session"]
        cleaned = _clean_task_title(row["title"])
        if not cleaned:
            continue
        if sid not in fallback_short_titles:
            fallback_short_titles[sid] = cleaned
        if sid not in session_titles and len(cleaned) > 6:
            session_titles[sid] = cleaned
    for sid, short_val in fallback_short_titles.items():
        session_titles.setdefault(sid, short_val)

    # Map session -> latest retrieval query
    session_queries: Dict[str, str] = {}
    for row in store.db.execute(
        "SELECT session,body FROM client_receipts WHERE client='retrieval' "
        "ORDER BY created_at DESC LIMIT 60"
    ):
        sid = row["session"]
        if sid and sid not in session_queries:
            try:
                q = str(json.loads(row["body"]).get("query", "")).strip()
                if q:
                    session_queries[sid] = q[:80]
            except ValueError:
                pass

    # Map session -> distinct used knowledge items
    session_knowledge: Dict[str, List[Dict[str, str]]] = {}
    for row in store.db.execute(
        "SELECT session,kind,subject,event,occurred_at FROM memory_events "
        "WHERE event IN ('retrieved','expanded','selected','executed') "
        "ORDER BY occurred_at DESC LIMIT 200"
    ):
        sid = row["session"]
        items = session_knowledge.setdefault(sid, [])
        if len(items) >= 4 or any(x["subject"] == row["subject"] for x in items):
            continue
        meta = lookup.get((row["kind"], row["subject"]), {})
        items.append({
            "kind": row["kind"],
            "subject": row["subject"],
            "title": meta.get("title", row["subject"])[:64],
            "event": row["event"],
            "time_ago": relative_time_zh(row["occurred_at"], clock),
        })

    # Live open sessions (heartbeat within 90s; fallback to recent task_runs if empty)
    sessions_out: List[Dict[str, Any]] = []
    seen_sessions = set()
    for row in store.db.execute(
        "SELECT * FROM live_sessions ORDER BY CASE WHEN state='active' THEN 0 ELSE 1 END, "
        "heartbeat_at DESC LIMIT 12"
    ):
        hb = parsed(row["heartbeat_at"])
        if not hb or (clock - hb).total_seconds() > 90:
            continue
        sid = row["session"]
        seen_sessions.add(sid)
        raw_live_title = _clean_task_title(row["title"])
        title = (
            raw_live_title if len(raw_live_title) > 6
            else session_titles.get(sid) or session_queries.get(sid) or raw_live_title or "等待新任务输入..."
        )
        client_raw = row["client"]
        client_label = "Codex" if "codex" in client_raw else "VS Code"
        ws_label = row["workspace"] or ("/".join(Path(row["cwd"]).parts[-2:]) if row["cwd"] else "未绑定目录")
        sessions_out.append({
            "session": sid,
            "session_short": "#" + sid[:8],
            "client": client_label,
            "workspace": ws_label,
            "title": title,
            "state": "active" if row["state"] == "active" else "idle",
            "last_query": session_queries.get(sid, ""),
            "knowledge_used": session_knowledge.get(sid, []),
            "updated_ago": relative_time_zh(row["updated_at"], clock),
        })
        if len(sessions_out) >= 6:
            break

    active_running_count = sum(1 for s in sessions_out if s["state"] == "active")

    # Recent knowledge hits & learning timeline
    event_labels = {
        "retrieved": ("命中召回", "hit"),
        "expanded": ("展开详情", "hit"),
        "selected": ("路由匹配", "hit"),
        "executed": ("零模型执行", "hit"),
        "verified_success": ("验证沉淀", "learned"),
    }
    kind_badges = {
        "memo": "避坑 Memo",
        "claim": "实证 Claim",
        "solution": "P/R/Q 契约",
    }
    activity_feed: List[Dict[str, Any]] = []
    seen_feed_keys = set()
    for row in store.db.execute(
        "SELECT id,workspace,kind,subject,version,event,session,occurred_at FROM memory_events "
        "WHERE event IN ('retrieved','expanded','selected','executed','verified_success') "
        "ORDER BY occurred_at DESC LIMIT 60"
    ):
        # Deduplicate paired claim+memo with identical subject in the same session within the same minute
        minute_bucket = str(row["occurred_at"])[:16]
        dedup_key = (row["session"], row["subject"], row["event"], minute_bucket)
        if dedup_key in seen_feed_keys:
            continue
        seen_feed_keys.add(dedup_key)
        meta = lookup.get((row["kind"], row["subject"]), {})
        action_zh, ev_type = event_labels.get(row["event"], (row["event"], "hit"))
        sid = row["session"]
        task_label = (
            "离线自动整理守护" if sid == "dream-pass"
            else "规则发布校验" if sid in ("author", "training", "ab-seed-train")
            else (session_titles.get(sid) or session_queries.get(sid, ""))
        )
        activity_feed.append({
            "id": row["id"],
            "event_type": ev_type,
            "kind": row["kind"],
            "badge": kind_badges.get(row["kind"], row["kind"]),
            "action": action_zh,
            "title": meta.get("title", row["subject"])[:80],
            "subject": row["subject"],
            "workspace": row["workspace"],
            "session_short": "#" + sid[:8] if len(sid) >= 8 and sid not in ("dream-pass", "author") else sid,
            "task_title": task_label[:72],
            "occurred_at": row["occurred_at"],
            "time_ago": relative_time_zh(row["occurred_at"], clock),
        })
        if len(activity_feed) >= 12:
            break

    latest_event_row = store.db.execute(
        "SELECT max(occurred_at) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed')"
    ).fetchone()
    last_hit_at = latest_event_row[0] if latest_event_row else None
    last_hit_ts = parsed(last_hit_at)
    just_hit = bool(last_hit_ts and (clock - last_hit_ts).total_seconds() <= 20)

    return {
        "schema": "llr.menubar.v1",
        "updated_at": clock.isoformat(),
        "machine_name": machine_name,
        "primary_workspace": primary_ws,
        "just_hit_knowledge": just_hit,
        "last_hit_ago": relative_time_zh(last_hit_at, clock) if last_hit_at else "尚无记录",
        "host": {
            "online": host_info["online"],
            "pid": host_info["pid"],
            "auto_dream": bool(policy.get("auto_dream_source", False)),
            "teams_away_only": bool(relay_cfg.get("enabled") and relay_cfg.get("away_only")),
            "teams_inbox": bool(inbox_cfg.get("enabled")),
        },
        "totals": {
            "today_hits": today_hits,
            "total_hits": total_hits,
            "memos": active_memos,
            "claims": active_claims,
            "solutions": verified_solutions,
            "themes": custom_themes,
            "open_sessions": len(sessions_out),
            "active_sessions": active_running_count,
        },
        "live_sessions": sessions_out,
        "recent_activity": activity_feed,
    }


def open_live_portal(store: Store, workspace: Optional[str] = None,
                     open_browser: bool = True) -> Dict[str, Any]:
    target_ws = workspace or _select_primary_workspace(store)
    require(bool(target_ws), "No registered workspace available to export portal")
    from .context import knowledge_revision
    rev = (knowledge_revision(store) or "empty")[:12]
    base_dir = store.home / "portal-live"
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    out_dir = base_dir / f"{target_ws.replace('/', '_')}-{rev}"
    index_html = out_dir / "index.html"
    if not index_html.is_file():
        if out_dir.exists():
            shutil.rmtree(out_dir)
        export_portal(store, target_ws, out_dir)
    if open_browser and sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", str(index_html)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "PORTAL_READY", "workspace": target_ws, "html": str(index_html)}


def swift_source_path(runtime_root: Optional[Path] = None) -> Path:
    base = (runtime_root or Path(__file__).resolve().parents[1]).resolve()
    src = base / "adapters" / "macos_menubar" / "CloneYourselfMenuBar.swift"
    require(src.is_file(), f"Menu bar Swift source missing: {src}")
    return src


def build_macos_menubar_app(home: Path, runtime_root: Optional[Path] = None) -> Path:
    require(sys.platform == "darwin", "Native menu bar icon is supported on macOS only")
    home = home.expanduser().resolve()
    require_private_location(home)
    src = swift_source_path(runtime_root)
    src_sha = file_digest(src)
    app_dir = home / "app" / "CloneYourselfMenuBar.app" / "Contents"
    macos_dir = app_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)
    binary = macos_dir / "CloneYourselfMenuBar"
    stamp_file = app_dir / "build.sha256"
    info_plist = app_dir / "Info.plist"
    plist_data = {
        "CFBundleExecutable": "CloneYourselfMenuBar",
        "CFBundleIdentifier": LAUNCH_AGENT_LABEL,
        "CFBundleName": "Clone Yourself",
        "CFBundleDisplayName": "Clone Yourself",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.4.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    }
    info_plist.write_bytes(plistlib.dumps(plist_data))
    if binary.is_file() and os.access(binary, os.X_OK) and stamp_file.is_file():
        if stamp_file.read_text(encoding="utf-8").strip() == src_sha:
            return binary
    swiftc = shutil.which("swiftc")
    cmd = [swiftc] if swiftc else ["/usr/bin/xcrun", "swiftc"]
    completed = subprocess.run(
        cmd + ["-O", "-parse-as-library", str(src), "-o", str(binary)],
        capture_output=True, text=True, timeout=90,
    )
    require(completed.returncode == 0,
            "Failed to compile macOS menu bar app: " + (completed.stderr or completed.stdout)[:500])
    binary.chmod(0o755)
    stamp_file.write_text(src_sha, encoding="utf-8")
    return binary


def _launcher_command_args(home: Path, binary: Path,
                           runtime_root: Optional[Path] = None) -> List[str]:
    from .clients import launcher_at
    root = (runtime_root or Path(__file__).resolve().parents[1]).resolve()
    launcher = launcher_at(root)
    return [
        str(binary),
        "--home", str(home.expanduser().resolve()),
        "--python", sys.executable,
        "--llr", str(launcher),
    ]


def install_launch_agent(home: Path, binary: Path,
                         runtime_root: Optional[Path] = None) -> Path:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / (LAUNCH_AGENT_LABEL + ".plist")
    argv = _launcher_command_args(home, binary, runtime_root)
    plist_body = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "StandardOutPath": str(home.expanduser().resolve() / "menubar.log"),
        "StandardErrorPath": str(home.expanduser().resolve() / "menubar.log"),
    }
    plist_path.write_bytes(plistlib.dumps(plist_body))
    return plist_path


def uninstall_launch_agent() -> bool:
    plist_path = Path.home() / "Library" / "LaunchAgents" / (LAUNCH_AGENT_LABEL + ".plist")
    if plist_path.is_file():
        plist_path.unlink()
        return True
    return False


def _set_menubar_policy_flag(home: Path, enabled: bool) -> None:
    policy_path = home.expanduser().resolve() / "automation.json"
    data = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else {}
    data["menubar_enabled"] = bool(enabled)
    policy_path.write_text(canonical(data), encoding="utf-8")
    policy_path.chmod(0o600)


def start_menubar(home: Path, approve: bool, install_login: bool = False,
                  runtime_root: Optional[Path] = None) -> Dict[str, Any]:
    require(approve, "Explicit approval required to build/start the desktop menu bar app")
    require(sys.platform == "darwin", "Menu bar status item is only available on macOS")
    home = home.expanduser().resolve()
    require_private_location(home)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = build_macos_menubar_app(home, runtime_root)
    _set_menubar_policy_flag(home, True)
    plist_path = None
    if install_login:
        plist_path = install_launch_agent(home, binary, runtime_root)
    current = probe_menubar_running(home)
    if current["running"]:
        return {
            "status": "ALREADY_RUNNING",
            "pid": current["pid"],
            "binary": str(binary),
            "login_agent_installed": current["login_agent_installed"],
        }
    argv = _launcher_command_args(home, binary, runtime_root)
    with (home / "menubar.log").open("ab") as log_stream:
        subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=log_stream, stderr=log_stream,
            start_new_session=True,
        )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        probed = probe_menubar_running(home)
        if probed["running"]:
            return {
                "status": "STARTED",
                "pid": probed["pid"],
                "binary": str(binary),
                "login_agent_installed": probed["login_agent_installed"],
                "login_agent_path": str(plist_path) if plist_path else probed["login_agent_path"],
            }
        time.sleep(0.08)
    raise RuntimeErrorBase("Menu bar app did not acquire lock within 5s; inspect private menubar.log")


def ensure_menubar_if_enabled(home: Path) -> None:
    if sys.platform != "darwin":
        return
    home = home.expanduser().resolve()
    policy_path = home / "automation.json"
    if not policy_path.is_file():
        return
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if policy.get("menubar_enabled") is not True:
            return
        if probe_menubar_running(home)["running"]:
            return
        binary = home / "app" / "CloneYourselfMenuBar.app" / "Contents" / "MacOS" / "CloneYourselfMenuBar"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            return
        argv = _launcher_command_args(home, binary)
        with (home / "menubar.log").open("ab") as log_stream:
            subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=log_stream, stderr=log_stream,
                start_new_session=True,
            )
    except (OSError, ValueError, RuntimeErrorBase):
        return


def stop_menubar(home: Path, disable_autostart: bool = True,
                 uninstall_login: bool = False) -> Dict[str, Any]:
    home = home.expanduser().resolve()
    if disable_autostart and (home / "automation.json").is_file():
        _set_menubar_policy_flag(home, False)
    removed_agent = uninstall_launch_agent() if uninstall_login else False
    probed = probe_menubar_running(home)
    if not probed["running"] or not probed["pid"]:
        return {"status": "NOT_RUNNING", "login_agent_removed": removed_agent}
    pid = probed["pid"]
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if not probe_menubar_running(home)["running"]:
            return {"status": "STOPPED", "pid": pid, "login_agent_removed": removed_agent}
        time.sleep(0.05)
    raise RuntimeErrorBase(f"Menu bar process {pid} did not exit within 4 seconds")
