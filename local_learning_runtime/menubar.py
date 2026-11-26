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

from .core import (RuntimeErrorBase, Store, canonical, file_digest, now,
                   require, require_private_location, verify_anchor, workspace_file)
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
        "SELECT session,workspace,kind,subject,event,occurred_at FROM memory_events "
        "WHERE event IN ('retrieved','expanded','selected','executed') "
        "ORDER BY occurred_at DESC LIMIT 200"
    ):
        sid = row["session"]
        items = session_knowledge.setdefault(sid, [])
        if len(items) >= 4 or any(x["subject"] == row["subject"] and x["kind"] == row["kind"] for x in items):
            continue
        meta = lookup.get((row["kind"], row["subject"]), {})
        items.append({
            "kind": row["kind"],
            "subject": row["subject"],
            "workspace": row["workspace"] or meta.get("workspace", ""),
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
        title = str(prov.get("title") or spec["statement"][:96])
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

    # Usage statistics from memory_events (pure read)
    hit_row = store.db.execute(
        "SELECT count(*), max(occurred_at) FROM memory_events "
        "WHERE kind=? AND subject=? AND event IN ('retrieved','expanded','selected','executed')",
        (kind, subject),
    ).fetchone()
    hit_count = int(hit_row[0] or 0)
    last_hit_at = hit_row[1]

    md_parts = [
        f"# [{kind_badges.get(kind, kind)}] {title}",
        f"- ID: `{subject}`",
        f"- Workspace: `{resolved_ws}` · Version: `v{version}` · 状态: `{integrity_label}`",
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
        "sections": sections,
        "anchors": anchors_out,
        "usage": {
            "hit_count": hit_count,
            "last_hit_ago": relative_time_zh(last_hit_at, clock) if last_hit_at else "尚无调用",
            "updated_ago": relative_time_zh(updated_at_iso, clock) if updated_at_iso else "",
        },
        "copy_markdown": "\n".join(md_parts),
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
