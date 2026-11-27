"""macOS Menu Bar status item & live local-knowledge dashboard manager."""

import json
import os
import plistlib
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .core import (RuntimeErrorBase, Store, canonical, file_digest, now,
                   require, require_private_location, verify_anchor, workspace_file)
from .inbox import load_inbox_config
from .knowledge import export_portal
from .lifecycle import parsed
from .notifier import default_machine_name, load_notifier_config
from .relay import load_relay_config
from .tokens import build_token_profiles, evaluate_hit_events, fallback_profile, format_tokens

LAUNCH_AGENT_LABEL = "com.noomesh.menubar"
LEGACY_LAUNCH_AGENT_LABEL = "com.cloneyourself.menubar"


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


_FOLLOWUP_PREFIXES = (
    "为什么", "那你", "你可以", "你在", "目前进度", "如果", "请继续", "继续", "需要", "好的", "ok",
)


def _clean_task_title(raw_title: str) -> str:
    if not raw_title:
        return ""
    text = str(raw_title).strip()
    if text.startswith("<system_notification>"):
        return ""
    # Unwrap Codex attachment preamble to extract the user's actual request
    marker = "## My request for Codex:"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    elif text.startswith("# Files mentioned by the user"):
        lines_after = [
            ln for ln in text.splitlines()[1:]
            if ln.strip() and not ln.lstrip().startswith(("-", "*"))
        ]
        text = "\n".join(lines_after).strip()

    # Convert markdown links [label](url) -> label
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)\s]+\)", r"\1", text)
    # Compact Azure DevOps / GitHub PR, WorkItem, and Incident URLs into crisp human badges
    text = re.sub(r"https?://[^\s)>]+/pullrequest/(\d+)[^\s)>]*", r"PR #\1 ", text, flags=re.I)
    text = re.sub(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)[^\s)>]*", r"PR #\1 ", text, flags=re.I)
    text = re.sub(r"https?://[^\s)>]+/_workitems/edit/(\d+)[^\s)>]*", r"Bug #\1 ", text, flags=re.I)
    text = re.sub(r"https?://[^\s)>]+/incidents/details/(\d+)[^\s)>]*", r"IcM #\1 ", text, flags=re.I)
    # Strip remaining bare URLs so long URLs never swallow the human summary
    text = re.sub(r"https?://\S+", " ", text)
    # Strip markdown emphasis/code/heading noise
    text = re.sub(r"[*_`]+", "", text)

    chosen = ""
    for raw_ln in text.splitlines():
        ln = re.sub(r"^(?:#{1,6}\s+|>\s+|[-•]\s+)", "", raw_ln.strip())
        ln = re.sub(r"###\s*", " ", ln)
        ln = re.sub(r"\s+", " ", ln).strip(" ，,。:：|-")
        if len(ln) >= 2:
            chosen = ln
            break
    return chosen[:96]


def humanize_knowledge_title(raw_title: Optional[str], statement: str = "",
                             fallback_id: str = "") -> str:
    candidate = str(raw_title or "").strip()
    stmt = str(statement or "").strip()
    probe = candidate or stmt
    ui_match = re.search(
        r"unique enabled UI node\s+(?:[A-Za-z0-9_.]+:id/)?([A-Za-z0-9_.-]+)",
        probe,
        re.I,
    )
    if ui_match and (not candidate or candidate.lower().startswith("in this recorded run")):
        view_id = ui_match.group(1)
        return f"UI 节点实测校验：{view_id} 启动前后唯一且可用"
    if not candidate and stmt.lower().startswith("recorded runtime predicates"):
        return f"运行时断言实测：{fallback_id[:28]}"
    if candidate.lower().startswith("user-authorized "):
        candidate = candidate[len("User-authorized "):].strip()
    return re.sub(r"\s+", " ", candidate or stmt or fallback_id)[:240]


def _ide_session_titles(session_ids: Iterable[str]) -> Dict[str, str]:
    sids = [s for s in session_ids if s]
    if not sids:
        return {}
    resolved: Dict[str, str] = {}
    copilot_base = Path.home() / ".copilot" / "session-state"
    if copilot_base.is_dir():
        for sid in sids:
            sdir = copilot_base / sid
            if not sdir.is_dir():
                continue
            ws_yaml = sdir / "workspace.yaml"
            yaml_title = ""
            if ws_yaml.is_file():
                try:
                    lines = ws_yaml.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for idx, ln in enumerate(lines):
                        if ln.startswith("name:"):
                            rest = ln[5:].strip().strip("'\"")
                            if rest in ("|-", "|", ">-", ">", ""):
                                block = []
                                for sub in lines[idx + 1:]:
                                    if sub.startswith("  "):
                                        block.append(sub.strip())
                                    else:
                                        break
                                yaml_title = _clean_task_title("\n".join(block))
                            else:
                                yaml_title = _clean_task_title(rest)
                            break
                except OSError:
                    pass
            cp_index = sdir / "checkpoints" / "index.md"
            cp_title = ""
            if cp_index.is_file():
                try:
                    rows = [
                        ln.strip() for ln in cp_index.read_text(encoding="utf-8", errors="ignore").splitlines()
                        if ln.strip().startswith("|") and not ln.strip().startswith("|---")
                    ]
                    if len(rows) >= 2:
                        cols = [c.strip() for c in rows[-1].strip("|").split("|")]
                        if len(cols) >= 2 and cols[1].lower() != "title":
                            cp_title = _clean_task_title(cols[1])
                except OSError:
                    pass
            if yaml_title:
                resolved[sid] = yaml_title
            elif cp_title:
                resolved[sid] = cp_title

    codex_db = Path.home() / ".codex" / "state_5.sqlite"
    missing = [s for s in sids if s not in resolved]
    if missing and codex_db.is_file():
        try:
            conn = sqlite3.connect(f"file:{codex_db}?mode=ro", uri=True, timeout=0.5)
            try:
                placeholders = ",".join("?" for _ in missing)
                for row in conn.execute(
                    f"SELECT id, coalesce(nullif(name,''), title, first_user_message) "
                    f"FROM threads WHERE id IN ({placeholders})",
                    missing,
                ):
                    cleaned = _clean_task_title(row[1] or "")
                    if cleaned:
                        resolved[row[0]] = cleaned
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    return resolved


def _is_context_free_followup(title: str) -> bool:
    if not title:
        return True
    low = title.strip().lower()
    if len(low) <= 10:
        return True
    return any(low.startswith(p) for p in _FOLLOWUP_PREFIXES)


def _knowledge_title_catalog(store: Store) -> Dict[Tuple[str, str], Dict[str, str]]:
    lookup: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in store.db.execute("SELECT id,body FROM claims"):
        try:
            body = json.loads(row["body"])
            spec = body["spec"]
            raw_t = spec.get("provenance", {}).get("title")
            title = humanize_knowledge_title(raw_t, spec["statement"], row["id"])
            lookup[("claim", row["id"])] = {
                "title": title,
                "workspace": spec["workspace"],
                "lifecycle": body.get("lifecycle", "active"),
            }
        except (KeyError, ValueError):
            continue
    for row in store.db.execute("SELECT workspace,id,lifecycle,body FROM memos"):
        try:
            spec = json.loads(row["body"])
            lookup[("memo", row["id"])] = {
                "title": humanize_knowledge_title(spec.get("title"), "", row["id"]),
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
                "title": humanize_knowledge_title(spec.get("problem", {}).get("goal"), "", row["id"]),
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
    tok_profiles = build_token_profiles(store)
    tok_eval = evaluate_hit_events(store, tok_profiles, day_ago)
    ev_savings = tok_eval["event_savings"]
    sess_saved_map = tok_eval["session_saved_totals"]
    sess_item_saved = tok_eval["session_item_saved"]

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
        "AND session != 'bundle-import' AND occurred_at >= ?",
        (day_ago,),
    ).fetchone()[0]
    total_hits = store.db.execute(
        "SELECT count(*) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND session != 'bundle-import'"
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

    # Map session -> distinct used knowledge items (merge paired Memo + Claim of the same rule)
    session_knowledge: Dict[str, List[Dict[str, Any]]] = {}
    seen_session_pairs: Dict[str, set] = {}
    for row in store.db.execute(
        "SELECT id,session,workspace,kind,subject,event,occurred_at FROM memory_events "
        "WHERE event IN ('retrieved','expanded','selected','executed') "
        "ORDER BY occurred_at DESC, id ASC LIMIT 240"
    ):
        sid = row["session"]
        pair_Set = seen_session_pairs.setdefault(sid, set())
        exact_pair = (row["kind"], row["subject"])
        if exact_pair in pair_Set:
            continue
        pair_Set.add(exact_pair)

        items = session_knowledge.setdefault(sid, [])
        meta = lookup.get(exact_pair, {})
        item_title = meta.get("title", row["subject"])[:160]
        prof = tok_profiles.get(exact_pair) or fallback_profile(
            row["kind"], row["subject"], row["workspace"]
        )
        ev_tok = ev_savings.get(row["id"], {})
        saved_val = int(sess_item_saved.get(
            (sid, row["kind"], row["subject"]),
            ev_tok.get("saved_tokens", prof["saved_per_hit"]),
        ))
        acq_val = int(prof["acquisition_tokens"])
        stem = row["subject"][:-6] if row["subject"].endswith("-claim") else row["subject"]

        existing_match = next(
            (
                x for x in items
                if (x["subject"][:-6] if x["subject"].endswith("-claim") else x["subject"]) == stem
                or x["title"] == item_title
            ),
            None,
        )
        if existing_match is not None:
            combined_saved = int(existing_match["saved_tokens"]) + saved_val
            existing_match["saved_tokens"] = combined_saved
            existing_match["saved_formatted"] = format_tokens(combined_saved)
            if row["kind"] == "memo" and existing_match["kind"] != "memo":
                existing_match["kind"] = "memo"
                existing_match["subject"] = row["subject"]
                existing_match["title"] = item_title
            continue

        if len(items) >= 4:
            continue
        items.append({
            "kind": row["kind"],
            "subject": row["subject"],
            "workspace": row["workspace"] or meta.get("workspace", ""),
            "title": item_title,
            "event": row["event"],
            "time_ago": relative_time_zh(row["occurred_at"], clock),
            "saved_tokens": saved_val,
            "saved_formatted": format_tokens(saved_val),
            "acquisition_tokens": acq_val,
            "acquisition_formatted": format_tokens(acq_val),
        })

    # Live open sessions: filter heartbeat freshness BEFORE sorting/limiting, and sort deterministically
    # by active state -> last real interaction (updated_at) -> session ID (never by jittery heartbeat_at!).
    hb_cutoff_iso = (clock - timedelta(seconds=90)).isoformat()
    live_rows = store.db.execute(
        "SELECT * FROM live_sessions WHERE heartbeat_at >= ? "
        "ORDER BY CASE WHEN state='active' THEN 0 ELSE 1 END, updated_at DESC, session ASC LIMIT 20",
        (hb_cutoff_iso,),
    ).fetchall()
    ide_titles = _ide_session_titles([r["session"] for r in live_rows])

    sessions_out: List[Dict[str, Any]] = []
    seen_sessions = set()
    for row in live_rows:
        hb = parsed(row["heartbeat_at"])
        if not hb or (clock - hb).total_seconds() > 90:
            continue
        sid = row["session"]
        seen_sessions.add(sid)
        curated_display = str(row["display_title"] or "").strip() if "display_title" in row.keys() else ""
        raw_live_title = _clean_task_title(row["title"])
        ide_topic = ide_titles.get(sid, "")
        if curated_display:
            title = curated_display
        elif _is_context_free_followup(raw_live_title) and ide_topic:
            title = ide_topic
        else:
            title = (
                raw_live_title if len(raw_live_title) > 6
                else ide_topic or session_titles.get(sid) or session_queries.get(sid) or raw_live_title or "等待新任务输入..."
            )
        session_titles[sid] = title
        client_raw = row["client"]
        client_label = "Codex" if "codex" in client_raw else "VS Code"
        ws_label = row["workspace"] or ("/".join(Path(row["cwd"]).parts[-2:]) if row["cwd"] else "未绑定目录")
        sess_saved = int(sess_saved_map.get(sid, 0))
        sessions_out.append({
            "session": sid,
            "session_short": "#" + sid[:8],
            "client": client_label,
            "workspace": ws_label,
            "title": title,
            "state": "active" if row["state"] == "active" else "idle",
            "last_query": session_queries.get(sid, ""),
            "knowledge_used": session_knowledge.get(sid, []),
            "saved_tokens": sess_saved,
            "saved_formatted": format_tokens(sess_saved),
            "updated_ago": relative_time_zh(row["updated_at"], clock),
        })
        if len(sessions_out) >= 20:
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
        prof = tok_profiles.get((row["kind"], row["subject"])) or fallback_profile(
            row["kind"], row["subject"], row["workspace"]
        )
        ev_tok = ev_savings.get(row["id"], {})
        saved_val = int(ev_tok.get("saved_tokens", prof["saved_per_hit"])) if ev_type == "hit" else 0
        acq_val = int(prof["acquisition_tokens"])
        token_badge = (
            f"沉淀耗 ~{format_tokens(acq_val)}"
            if ev_type == "learned"
            else f"省 ~{format_tokens(saved_val)}"
        )
        activity_feed.append({
            "id": row["id"],
            "event_type": ev_type,
            "kind": row["kind"],
            "badge": kind_badges.get(row["kind"], row["kind"]),
            "action": action_zh,
            "title": meta.get("title", row["subject"])[:180],
            "subject": row["subject"],
            "workspace": row["workspace"],
            "session_short": "#" + sid[:8] if len(sid) >= 8 and sid not in ("dream-pass", "author") else sid,
            "task_title": task_label[:120],
            "occurred_at": row["occurred_at"],
            "time_ago": relative_time_zh(row["occurred_at"], clock),
            "saved_tokens": saved_val,
            "acquisition_tokens": acq_val,
            "token_badge": token_badge,
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
        "gas_gauge": tok_eval["gas_gauge"],
        "live_sessions": sessions_out,
        "recent_activity": activity_feed,
    }


def _resolve_anchor_details(store: Store, workspace: str,
                            anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        ws_root = Path(store.workspace(workspace)["root"]).resolve()
    except (RuntimeErrorBase, OSError):
        ws_root = None
    resolved_list: List[Dict[str, Any]] = []
    for anchor in anchors:
        rel_path = str(anchor.get("path", ""))
        sha_val = str(anchor.get("sha256", ""))
        symbols = [str(s) for s in anchor.get("symbols", [])]
        full_path: Optional[str] = None
        anchor_status = "UNKNOWN"
        status_label = "未校验"
        if ws_root is not None:
            try:
                candidate = workspace_file(ws_root, rel_path)
                if candidate.is_file():
                    full_path = str(candidate)
            except (RuntimeErrorBase, OSError):
                full_path = None
            try:
                ver = verify_anchor(ws_root, anchor, require_exact=False)
                anchor_status = ver.get("status", "PASS")
                status_label = "符号仍在 (局部改动)" if anchor_status == "SOFT_DRIFT" else "源码一致"
            except (RuntimeErrorBase, OSError, ValueError):
                anchor_status = "BLOCKED"
                status_label = "已变更 / 缺失"
        resolved_list.append({
            "path": rel_path,
            "full_path": full_path,
            "sha256_short": sha_val[:12],
            "symbols": symbols,
            "status": anchor_status,
            "status_label": status_label,
        })
    return resolved_list


def _format_kv_lines(mapping: Dict[str, Any]) -> str:
    if not mapping:
        return "（无特定限制）"
    lines = []
    for k, v in sorted(mapping.items()):
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        lines.append(f"• {k}: {val_str}")
    return "\n".join(lines)


def menubar_item_detail(store: Store, kind: str, subject: str,
                        workspace: Optional[str] = None) -> Dict[str, Any]:
    require(kind in ("memo", "claim", "solution"), "Unknown knowledge item kind")
    require(isinstance(subject, str) and 0 < len(subject.strip()) <= 200, "Invalid knowledge subject")
    clock = datetime.now(timezone.utc)
    kind_badges = {
        "memo": "避坑 Memo",
        "claim": "实证 Claim",
        "solution": "P/R/Q 契约",
    }

    sections: List[Dict[str, str]] = []
    anchors_out: List[Dict[str, Any]] = []
    tags: List[str] = []
    integrity_status = "PASS"
    integrity_label = "校验通过"
    version = 1
    lifecycle = "active"
    updated_at_iso = ""
    title = subject
    resolved_ws = workspace or ""

    if kind == "memo":
        from .memos import verify_memo_live
        if workspace:
            row = store.db.execute(
                "SELECT * FROM memos WHERE workspace=? AND id=?", (workspace, subject)
            ).fetchone()
        else:
            row = store.db.execute(
                "SELECT * FROM memos WHERE id=? ORDER BY updated_at DESC LIMIT 1", (subject,)
            ).fetchone()
        require(row is not None, f"Memo not found: {subject}")
        resolved_ws = row["workspace"]
        version = int(row["version"])
        lifecycle = str(row["lifecycle"])
        updated_at_iso = str(row["updated_at"] or "")
        spec = json.loads(row["body"])
        title = str(spec.get("title", subject))
        memo_subkind = "避坑警示 (Trap)" if spec.get("kind") == "trap" else "架构规则 (Rule)"
        tags = [memo_subkind] + [f"#{t}" for t in spec.get("tags", [])]
        try:
            ver = verify_memo_live(store, resolved_ws, subject)
            if ver.get("anchor_drift"):
                integrity_status = "SOFT_DRIFT"
                integrity_label = "软漂移可用 · 关键符号仍在"
            else:
                integrity_status = "PASS"
                integrity_label = "校验通过 · 锚点一致"
        except (RuntimeErrorBase, OSError, ValueError) as exc:
            integrity_status = "BLOCKED"
            integrity_label = "已阻断 · " + str(exc)[:72]

        ver_obj = spec.get("verification", {})
        sections = [
            {"label": "触发场景 (When)", "icon": "scope", "body": str(spec.get("when", ""))},
            {"label": "核心规则与做法 (Rule)", "icon": "checkmark.shield", "body": str(spec.get("rule", ""))},
            {"label": "常见踩坑与后果 (Trap)", "icon": "exclamationmark.triangle", "body": str(spec.get("trap", ""))},
            {
                "label": f"验证依据 ({ver_obj.get('method', 'source_symbol_contract')})",
                "icon": "checkmark.seal",
                "body": str(ver_obj.get("summary", "")),
            },
        ]
        anchors_out = _resolve_anchor_details(store, resolved_ws, ver_obj.get("anchors", []))

    elif kind == "claim":
        row = store.db.execute("SELECT id,body FROM claims WHERE id=?", (subject,)).fetchone()
        require(row is not None, f"Claim not found: {subject}")
        claim_obj = json.loads(row["body"])
        spec = claim_obj["spec"]
        resolved_ws = str(spec["workspace"])
        version = int(claim_obj.get("version", 1))
        lifecycle = str(claim_obj.get("lifecycle", "active"))
        prov = spec.get("provenance", {})
        updated_at_iso = str(prov.get("validated_at", ""))
        title = humanize_knowledge_title(prov.get("title"), spec["statement"], subject)
        tags = [f"类型: {spec.get('task_type', 'general')}"]
        if prov.get("kind"):
            tags.append(f"来源: {prov['kind']}")

        snapshot = store.snapshot()
        try:
            store.current_valid(claim_obj, snapshot, spec["conditions"])
            drift = store.verify(spec).get("anchor_drift", [])
            if drift:
                integrity_status = "SOFT_DRIFT"
                integrity_label = "软漂移可用 · 关键符号仍在"
            else:
                integrity_status = "PASS"
                integrity_label = "校验通过 · 证据与锚点有效"
        except (RuntimeErrorBase, OSError, ValueError) as exc:
            integrity_status = "BLOCKED"
            integrity_label = "已阻断 · " + str(exc)[:72]

        check_lines: List[str] = []
        seen_evidence = set()
        for idx, chk in enumerate(spec.get("checks", []), 1):
            c_kind = chk.get("kind", "predicate")
            eid = str(chk.get("evidence_id", ""))
            if c_kind == "json_equals":
                exp = json.dumps(chk.get("expected"), ensure_ascii=False)
                check_lines.append(f"{idx}. [JSON 断言] {chk.get('pointer')} == {exp}")
            else:
                sel = json.dumps(chk.get("selector", chk), ensure_ascii=False)
                check_lines.append(f"{idx}. [{c_kind}] {sel}")
            if eid and eid not in seen_evidence:
                seen_evidence.add(eid)
                try:
                    ev_row, raw_bytes = store.evidence(eid)
                    mtype = ev_row["media_type"]
                    if mtype == "application/json":
                        pretty = json.dumps(json.loads(raw_bytes), ensure_ascii=False, indent=2)
                        if len(pretty) > 700:
                            pretty = pretty[:700] + "\n  … (证据已截断展示)"
                        check_lines.append(f"   证据摘录 ({eid[:12]}):\n{pretty}")
                    else:
                        check_lines.append(
                            f"   二进制/结构化证据 ({mtype}, {len(raw_bytes)} bytes, sha256: {ev_row['sha256'][:12]})"
                        )
                except (RuntimeErrorBase, OSError, ValueError):
                    check_lines.append(f"   证据引用: {eid[:16]} (本地文件未命中)")

        prov_lines = []
        if prov.get("task"):
            prov_lines.append(f"• 沉淀任务: {prov['task']}")
        if prov.get("kind"):
            prov_lines.append(f"• 验证方式: {prov['kind']}")
        if prov.get("session"):
            prov_lines.append(f"• 来源会话: #{str(prov['session'])[:8]}")
        if prov.get("validated_at"):
            prov_lines.append(f"• 验证时间: {str(prov['validated_at'])[:19].replace('T', ' ')} UTC")
        ver_meaning = claim_obj.get("verification", {}).get("meaning")
        if ver_meaning:
            prov_lines.append(f"• 边界说明: {ver_meaning}")

        sections = [
            {"label": "完整经验结论 (Statement)", "icon": "doc.text", "body": str(spec["statement"])},
            {"label": "适用条件边界 (Conditions)", "icon": "slider.horizontal.3",
             "body": _format_kv_lines(spec.get("conditions", {}))},
            {"label": "本地断言与证据 (Predicates & Evidence)", "icon": "checkmark.rectangle.stack",
             "body": "\n".join(check_lines) if check_lines else "（无断言明细）"},
        ]
        if prov_lines:
            sections.append({
                "label": "验证溯源 (Provenance)",
                "icon": "clock.arrow.circlepath",
                "body": "\n".join(prov_lines),
            })
        anchors_out = _resolve_anchor_details(store, resolved_ws, spec.get("anchors", []))

    else:  # solution
        from .solutions import check_preconditions, get_solution
        if workspace:
            row = store.db.execute(
                "SELECT workspace,id FROM solutions WHERE workspace=? AND id=?", (workspace, subject)
            ).fetchone()
        else:
            row = store.db.execute(
                "SELECT workspace,id FROM solutions WHERE id=? ORDER BY updated_at DESC LIMIT 1", (subject,)
            ).fetchone()
        require(row is not None, f"Solution not found: {subject}")
        resolved_ws = row["workspace"]
        record = get_solution(store, resolved_ws, subject)
        spec = record["spec"]
        version = int(record["version"])
        lifecycle = str(record["lifecycle"])
        updated_at_iso = str(record.get("updated_at") or "")
        prob = spec.get("problem", {})
        title = str(prob.get("goal", subject))
        tags = [f"状态: {lifecycle}"]
        if prob.get("intent"):
            tags.append(f"意图: {prob['intent']}")

        if lifecycle != "verified":
            integrity_status = "BLOCKED"
            integrity_label = f"尚未完全验证 ({lifecycle})"
        elif not spec.get("parameters"):
            pre_res = check_preconditions(store, record, {}, {})
            if any(r["verdict"] == "FAIL" for r in pre_res):
                integrity_status = "BLOCKED"
                integrity_label = "前置条件或基线指纹不匹配"
            else:
                integrity_status = "PASS"
                integrity_label = "已验证契约 · 零模型可执行"
        else:
            integrity_status = "PASS"
            integrity_label = "已验证契约 · 按已审批参数枚举执行"

        pattern_lines = [f"目标: {prob.get('goal', '')}"]
        if prob.get("patterns"):
            pattern_lines.append("匹配指令模板:")
            for pat in prob["patterns"]:
                pattern_lines.append(f"  • {pat}")
        if spec.get("parameters"):
            pattern_lines.append("枚举参数边界:")
            for pname, pdef in spec["parameters"].items():
                enums = ", ".join(json.dumps(x, ensure_ascii=False) for x in pdef.get("enum", []))
                pattern_lines.append(f"  • {pname} ({pdef.get('type')}): [{enums}]")

        pre_lines = []
        sol_anchors = []
        for idx, pre in enumerate(spec.get("preconditions", []), 1):
            pkind = pre.get("kind")
            if pkind == "file_sha256" and isinstance(pre.get("path"), str) and isinstance(pre.get("expected"), str):
                pre_lines.append(f"{idx}. [文件指纹] {pre['path']} == {pre['expected'][:16]}…")
                sol_anchors.append({"path": pre["path"], "sha256": pre["expected"], "symbols": []})
            else:
                pre_lines.append(f"{idx}. {json.dumps(pre, ensure_ascii=False)}")

        step_lines = []
        for idx, st in enumerate(spec.get("steps", []), 1):
            op = st.get("op", "step")
            sid = st.get("id", str(idx))
            detail = st.get("path") or st.get("runner") or ""
            step_lines.append(f"{idx}. [{op}] ({sid}) {detail}".strip())

        post_lines = []
        for idx, post in enumerate(spec.get("postconditions", []), 1):
            exp = json.dumps(post.get("expected"), ensure_ascii=False)
            post_lines.append(f"{idx}. 步骤 {post.get('step')}{post.get('pointer')} == {exp}")
        if spec.get("boundaries"):
            post_lines.append("明确不承诺边界:")
            for b in spec["boundaries"]:
                post_lines.append(f"  • {b}")

        sections = [
            {"label": "解决目标与触发模板 (Goal & Patterns)", "icon": "bolt.horizontal",
             "body": "\n".join(pattern_lines)},
            {"label": "前置条件 P (Preconditions)", "icon": "lock.shield",
             "body": "\n".join(pre_lines) if pre_lines else "（无额外前置条件）"},
            {"label": "确定性执行步骤 R (Steps)", "icon": "list.number",
             "body": "\n".join(step_lines) if step_lines else "（无步骤）"},
            {"label": "结果断言 Q 与边界 (Postconditions & Boundaries)", "icon": "checkmark.seal",
             "body": "\n".join(post_lines) if post_lines else "（无后置断言）"},
        ]
        anchors_out = _resolve_anchor_details(store, resolved_ws, sol_anchors)

    # Usage statistics & Token Economics from memory_events (pure read)
    hit_row = store.db.execute(
        "SELECT count(*), max(occurred_at) FROM memory_events "
        "WHERE kind=? AND subject=? AND event IN ('retrieved','expanded','selected','executed')",
        (kind, subject),
    ).fetchone()
    hit_count = int(hit_row[0] or 0)
    last_hit_at = hit_row[1]

    tok_profiles = build_token_profiles(store)
    day_ago_iso = (clock - timedelta(hours=24)).isoformat()
    tok_eval = evaluate_hit_events(store, tok_profiles, day_ago_iso)
    prof = tok_profiles.get((kind, subject)) or fallback_profile(kind, subject, resolved_ws)
    cum_saved = int(tok_eval["item_saved_totals"].get((kind, subject), 0))
    tokens_summary = {
        "acquisition_tokens": int(prof["acquisition_tokens"]),
        "acquisition_formatted": format_tokens(prof["acquisition_tokens"]),
        "injected_tokens": int(prof["injected_tokens"]),
        "saved_per_hit": int(prof["saved_per_hit"]),
        "saved_per_hit_formatted": format_tokens(prof["saved_per_hit"]),
        "cumulative_saved_tokens": cum_saved,
        "cumulative_saved_formatted": format_tokens(cum_saved),
        "basis": str(prof["basis"]),
        "basis_label": str(prof["basis_label"]),
    }

    md_parts = [
        f"# [{kind_badges.get(kind, kind)}] {title}",
        f"- ID: `{subject}`",
        f"- Workspace: `{resolved_ws}` · Version: `v{version}` · 状态: `{integrity_label}`",
        (
            f"- Token 账本 ({tokens_summary['basis_label']}): "
            f"首次获取耗 `~{tokens_summary['acquisition_formatted']}` · "
            f"注入开销 `{tokens_summary['injected_tokens']}` · "
            f"单次净省 `~{tokens_summary['saved_per_hit_formatted']}` · "
            f"累计复用已省 `~{tokens_summary['cumulative_saved_formatted']}`"
        ),
    ]
    for sec in sections:
        md_parts.append(f"\n## {sec['label']}\n{sec['body']}")
    if anchors_out:
        md_parts.append("\n## 绑定源码锚点")
        for a in anchors_out:
            sym_txt = f" (symbols: {', '.join(a['symbols'])})" if a["symbols"] else ""
            md_parts.append(f"- `{a['path']}` [{a['status_label']}]{sym_txt}")

    return {
        "schema": "llr.menubar_item.v1",
        "kind": kind,
        "badge": kind_badges.get(kind, kind),
        "id": subject,
        "workspace": resolved_ws,
        "title": title,
        "version": version,
        "lifecycle": lifecycle,
        "integrity_status": integrity_status,
        "integrity_label": integrity_label,
        "tags": tags,
        "tokens": tokens_summary,
        "sections": sections,
        "anchors": anchors_out,
        "usage": {
            "hit_count": hit_count,
            "last_hit_ago": relative_time_zh(last_hit_at, clock) if last_hit_at else "尚无调用",
            "updated_ago": relative_time_zh(updated_at_iso, clock) if updated_at_iso else "",
        },
        "copy_markdown": "\n".join(md_parts),
    }


def _channel_meta(event_name: str, source_raw: str) -> Tuple[str, str]:
    prefix = str(source_raw or "").split(":", 1)[0].strip().lower()
    if event_name == "executed" or prefix == "execute":
        return ("execute", "零模型执行")
    if event_name == "selected" or prefix == "route":
        return ("route", "契约路由匹配")
    if event_name == "expanded" or prefix == "expand":
        return ("context", "L3 展开详情")
    if prefix == "session_index":
        return ("session_index", "L0 开场自动注入")
    if prefix == "context":
        return ("context", "L1/L2 分层上下文")
    if prefix == "search":
        return ("search", "MCP 主动检索")
    if prefix == "recall":
        return ("recall", "精确条件召回")
    return ("other", "本地记忆调用")


def _local_clock_hm(iso_value: Optional[str]) -> str:
    ts = parsed(iso_value)
    if ts is None:
        return ""
    return ts.astimezone().strftime("%H:%M:%S")


def menubar_hits_detail(store: Store, hours: int = 24) -> Dict[str, Any]:
    require(isinstance(hours, int) and 1 <= hours <= 720, "Hours window must be between 1 and 720")
    clock = datetime.now(timezone.utc)
    cutoff_iso = (clock - timedelta(hours=hours)).isoformat()
    lookup = _knowledge_title_catalog(store)
    tok_profiles = build_token_profiles(store)
    tok_eval = evaluate_hit_events(store, tok_profiles, cutoff_iso)
    ev_savings = tok_eval["event_savings"]
    gas = tok_eval["gas_gauge"]

    total_hits = int(store.db.execute(
        "SELECT count(*) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND session != 'bundle-import'"
    ).fetchone()[0] or 0)
    window_hits = int(store.db.execute(
        "SELECT count(*) FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND session != 'bundle-import' AND occurred_at >= ?",
        (cutoff_iso,),
    ).fetchone()[0] or 0)

    # Session -> descriptive task title
    session_titles: Dict[str, str] = {}
    fallback_short_titles: Dict[str, str] = {}
    for row in store.db.execute(
        "SELECT session,title FROM task_runs ORDER BY updated_at DESC LIMIT 250"
    ):
        sid = row["session"]
        cleaned = _clean_task_title(row["title"])
        if not cleaned:
            continue
        if sid not in fallback_short_titles:
            fallback_short_titles[sid] = cleaned
        if sid not in session_titles and len(cleaned) > 6:
            session_titles[sid] = cleaned
    for row in store.db.execute("SELECT session,title,display_title FROM live_sessions"):
        sid = row["session"]
        curated = str(row["display_title"] or "").strip()
        if curated:
            session_titles[sid] = curated
            continue
        cleaned = _clean_task_title(row["title"])
        if cleaned and len(cleaned) > 6 and sid not in session_titles:
            session_titles[sid] = cleaned
    for sid, short_val in fallback_short_titles.items():
        session_titles.setdefault(sid, short_val)
    ide_for_hits = _ide_session_titles(list(session_titles.keys()))
    for sid, ide_val in ide_for_hits.items():
        if sid not in session_titles or _is_context_free_followup(session_titles[sid]):
            session_titles[sid] = ide_val

    # Session retrieval receipts for matching search queries near event timestamp
    session_receipts: Dict[str, List[Tuple[datetime, str]]] = {}
    for row in store.db.execute(
        "SELECT session,created_at,body FROM client_receipts WHERE client='retrieval' "
        "ORDER BY created_at DESC LIMIT 150"
    ):
        sid = row["session"]
        ts = parsed(row["created_at"])
        if not sid or ts is None:
            continue
        try:
            q = str(json.loads(row["body"]).get("query", "")).strip()
        except ValueError:
            q = ""
        if q:
            session_receipts.setdefault(sid, []).append((ts, q[:96]))

    def _match_query(sid: str, occurred_iso: str, channel_key: str) -> str:
        if channel_key not in ("search", "context", "route"):
            return ""
        ev_ts = parsed(occurred_iso)
        if ev_ts is None:
            return ""
        candidates = session_receipts.get(sid, [])
        best_q = ""
        best_delta = 5.0
        for r_ts, q_text in candidates:
            delta = abs((ev_ts - r_ts).total_seconds())
            if delta <= best_delta:
                best_delta = delta
                best_q = q_text
        return best_q

    kind_badges = {
        "memo": "避坑 Memo",
        "claim": "实证 Claim",
        "solution": "P/R/Q 契约",
    }

    raw_rows = store.db.execute(
        "SELECT id,workspace,kind,subject,version,event,session,source,occurred_at "
        "FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND occurred_at >= ? ORDER BY occurred_at DESC LIMIT 120",
        (cutoff_iso,),
    ).fetchall()

    by_kind: Dict[str, int] = {"memo": 0, "claim": 0, "solution": 0}
    channel_counts: Dict[str, Dict[str, Any]] = {}
    top_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    distinct_sessions = set()
    events_out: List[Dict[str, Any]] = []

    for row in raw_rows:
        kind = row["kind"]
        subj = row["subject"]
        sid = row["session"]
        ws = row["workspace"]
        occ = row["occurred_at"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if sid:
            distinct_sessions.add(sid)

        ch_key, ch_label = _channel_meta(row["event"], row["source"])
        ch_entry = channel_counts.setdefault(ch_key, {"key": ch_key, "label": ch_label, "count": 0})
        ch_entry["count"] += 1

        meta = lookup.get((kind, subj), {})
        resolved_title = meta.get("title", subj)[:200]
        resolved_ws = ws or meta.get("workspace", "")
        prof = tok_profiles.get((kind, subj)) or fallback_profile(kind, subj, resolved_ws)
        ev_tok = ev_savings.get(row["id"], {})
        saved_val = int(ev_tok.get("saved_tokens", prof["saved_per_hit"]))
        acq_val = int(prof["acquisition_tokens"])

        t_key = (kind, subj)
        if t_key not in top_map:
            top_map[t_key] = {
                "kind": kind,
                "badge": kind_badges.get(kind, kind),
                "subject": subj,
                "workspace": resolved_ws,
                "title": resolved_title,
                "hit_count": 0,
                "window_saved_tokens": 0,
                "acquisition_tokens": acq_val,
                "sessions": set(),
                "last_occurred_at": occ,
                "last_hit_ago": relative_time_zh(occ, clock),
            }
        top_map[t_key]["hit_count"] += 1
        top_map[t_key]["window_saved_tokens"] += saved_val
        if sid:
            top_map[t_key]["sessions"].add(sid)

        matched_q = _match_query(sid, occ, ch_key)
        events_out.append({
            "id": row["id"],
            "kind": kind,
            "badge": kind_badges.get(kind, kind),
            "subject": subj,
            "workspace": resolved_ws,
            "title": resolved_title,
            "version": int(row["version"] or 1),
            "channel_key": ch_key,
            "channel_label": ch_label,
            "session_short": "#" + sid[:8] if len(sid) >= 8 else sid,
            "task_title": session_titles.get(sid, "")[:76],
            "query": matched_q,
            "local_time": _local_clock_hm(occ),
            "time_ago": relative_time_zh(occ, clock),
            "occurred_at": occ,
            "saved_tokens": saved_val,
            "saved_formatted": format_tokens(saved_val),
            "acquisition_formatted": format_tokens(acq_val),
        })

    top_items = sorted(
        top_map.values(),
        key=lambda item: (-item["window_saved_tokens"], -item["hit_count"], item["last_occurred_at"]),
    )
    top_items_clean = [
        {
            "kind": item["kind"],
            "badge": item["badge"],
            "subject": item["subject"],
            "workspace": item["workspace"],
            "title": item["title"],
            "hit_count": item["hit_count"],
            "sessions_count": len(item["sessions"]),
            "last_hit_ago": item["last_hit_ago"],
            "acquisition_tokens": item["acquisition_tokens"],
            "acquisition_formatted": format_tokens(item["acquisition_tokens"]),
            "window_saved_tokens": item["window_saved_tokens"],
            "window_saved_formatted": format_tokens(item["window_saved_tokens"]),
        }
        for item in top_items
    ]
    channels_sorted = sorted(channel_counts.values(), key=lambda c: -c["count"])

    # If 24h has 0 hits, include up to 8 most recent historical hits for context
    fallback_events: List[Dict[str, Any]] = []
    if not events_out:
        hist_rows = store.db.execute(
            "SELECT id,workspace,kind,subject,version,event,session,source,occurred_at "
            "FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
            "ORDER BY occurred_at DESC LIMIT 8"
        ).fetchall()
        for row in hist_rows:
            kind = row["kind"]
            subj = row["subject"]
            sid = row["session"]
            occ = row["occurred_at"]
            ch_key, ch_label = _channel_meta(row["event"], row["source"])
            meta = lookup.get((kind, subj), {})
            resolved_ws = row["workspace"] or meta.get("workspace", "")
            prof = tok_profiles.get((kind, subj)) or fallback_profile(kind, subj, resolved_ws)
            ev_tok = ev_savings.get(row["id"], {})
            saved_val = int(ev_tok.get("saved_tokens", prof["saved_per_hit"]))
            fallback_events.append({
                "id": row["id"],
                "kind": kind,
                "badge": kind_badges.get(kind, kind),
                "subject": subj,
                "workspace": resolved_ws,
                "title": meta.get("title", subj)[:200],
                "version": int(row["version"] or 1),
                "channel_key": ch_key,
                "channel_label": ch_label,
                "session_short": "#" + sid[:8] if len(sid) >= 8 else sid,
                "task_title": session_titles.get(sid, "")[:76],
                "query": _match_query(sid, occ, ch_key),
                "local_time": _local_clock_hm(occ),
                "time_ago": relative_time_zh(occ, clock),
                "occurred_at": occ,
                "saved_tokens": saved_val,
                "saved_formatted": format_tokens(saved_val),
                "acquisition_formatted": format_tokens(prof["acquisition_tokens"]),
            })

    md_lines = [
        f"# 近 {hours} 小时本地知识命中与 Token 节省报告",
        f"- 24h 净节省 Token: **~{gas['today_saved_formatted']}**（历史累计节省 **~{gas['total_saved_formatted']}**，投资回报比 **{gas['roi_multiple']}x**）",
        f"- 24h 命中总计: **{window_hits}** 次（历史累计复用 **{total_hits}** 次）",
        f"- 涉及知识条目: **{len(top_items_clean)}** 条 · 赋能会话: **{len(distinct_sessions)}** 个",
        f"- 类型分布: Memo ×{by_kind.get('memo', 0)} · Claim ×{by_kind.get('claim', 0)} · P/R/Q 契约 ×{by_kind.get('solution', 0)}",
    ]
    if channels_sorted:
        ch_summary = " · ".join(f"{c['label']} ×{c['count']}" for c in channels_sorted)
        md_lines.append(f"- 触发渠道: {ch_summary}")
    if top_items_clean:
        md_lines.append("\n## 高频命中与节省排行")
        for idx, t in enumerate(top_items_clean, 1):
            md_lines.append(
                f"{idx}. **[{t['badge']}] {t['title']}** — 命中 {t['hit_count']} 次 · "
                f"本窗口省 `~{t['window_saved_formatted']}`（首次获取耗 `~{t['acquisition_formatted']}`）"
            )
    if events_out:
        md_lines.append("\n## 逐条命中明细")
        for ev in events_out:
            q_part = f" · 检索词: `{ev['query']}`" if ev["query"] else ""
            md_lines.append(
                f"- `{ev['local_time']}` ({ev['time_ago']}) [{ev['channel_label']}] "
                f"**{ev['title']}** — 省 `~{ev['saved_formatted']}` (`{ev['session_short']}`){q_part}"
            )

    return {
        "schema": "llr.menubar_hits.v1",
        "window_hours": hours,
        "window_hits": window_hits,
        "total_hits": total_hits,
        "window_saved_tokens": gas["today_saved_tokens"],
        "window_saved_formatted": gas["today_saved_formatted"],
        "total_saved_tokens": gas["total_saved_tokens"],
        "total_saved_formatted": gas["total_saved_formatted"],
        "window_injected_tokens": gas["today_injected_tokens"],
        "roi_multiple": gas["roi_multiple"],
        "distinct_items": len(top_items_clean),
        "distinct_sessions": len(distinct_sessions),
        "by_kind": {
            "memo": by_kind.get("memo", 0),
            "claim": by_kind.get("claim", 0),
            "solution": by_kind.get("solution", 0),
        },
        "channels": channels_sorted,
        "top_items": top_items_clean,
        "events": events_out,
        "recent_fallback_events": fallback_events,
        "copy_markdown": "\n".join(md_lines),
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
    src = base / "adapters" / "macos_menubar" / "NooMeshMenuBar.swift"
    require(src.is_file(), f"Menu bar Swift source missing: {src}")
    return src


def build_macos_menubar_app(home: Path, runtime_root: Optional[Path] = None) -> Path:
    require(sys.platform == "darwin", "Native menu bar icon is supported on macOS only")
    home = home.expanduser().resolve()
    require_private_location(home)
    src = swift_source_path(runtime_root)
    src_sha = file_digest(src)
    app_dir = home / "app" / "NooMeshMenuBar.app" / "Contents"
    macos_dir = app_dir / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)
    binary = macos_dir / "NooMeshMenuBar"
    stamp_file = app_dir / "build.sha256"
    info_plist = app_dir / "Info.plist"
    plist_data = {
        "CFBundleExecutable": "NooMeshMenuBar",
        "CFBundleIdentifier": LAUNCH_AGENT_LABEL,
        "CFBundleName": "NooMesh",
        "CFBundleDisplayName": "NooMesh",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.4.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "NooMesh Portable Doctrine Bundle",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Owner",
            "CFBundleTypeExtensions": ["noomesh"],
        }],
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
    legacy_plist = agents_dir / (LEGACY_LAUNCH_AGENT_LABEL + ".plist")
    if legacy_plist.is_file():
        legacy_plist.unlink()
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
    removed = False
    for label in (LAUNCH_AGENT_LABEL, LEGACY_LAUNCH_AGENT_LABEL):
        plist_path = Path.home() / "Library" / "LaunchAgents" / (label + ".plist")
        if plist_path.is_file():
            plist_path.unlink()
            removed = True
    return removed


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
        binary = home / "app" / "NooMeshMenuBar.app" / "Contents" / "MacOS" / "NooMeshMenuBar"
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
