"""Honest token economics: acquisition cost vs per-hit & cumulative net token savings."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .core import RuntimeErrorBase, Store, now, require, workspace_file

# Realistic bounded per-file inspection window (agents read bounded slices + grep rather than multi-MB blobs)
MAX_SOURCE_FILE_INSPECTION_BYTES = 32_000
TOOL_ROUNDTRIP_OVERHEAD_TOKENS = 650
SESSION_INDEX_ORIENTATION_TOKENS = 950
SECONDARY_SHARED_ANCHOR_SAVINGS = 850

GAS_GAUGE_MILESTONES = (
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
    2_500_000,
    5_000_000,
    10_000_000,
    25_000_000,
    50_000_000,
)


def format_tokens(count: int) -> str:
    val = max(0, int(count))
    if val < 1_000:
        return str(val)
    if val < 1_000_000:
        k = val / 1_000.0
        return f"{k:.1f}K" if k < 100 else f"{int(round(k))}K"
    m = val / 1_000_000.0
    return f"{m:.2f}M" if m < 10 else f"{m:.1f}M"


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = 0
    ascii_and_other_bytes = 0
    for ch in text:
        cp = ord(ch)
        if 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF:
            cjk += 1
        else:
            ascii_and_other_bytes += len(ch.encode("utf-8"))
    return max(1, int(round(cjk * 0.9 + ascii_and_other_bytes / 4.0)))


def probe_copilot_turn_tokens(session_id: Optional[str]) -> Optional[int]:
    """Read incremental non-cached tokens from the latest turn of a local Copilot session if available."""
    if not session_id or len(session_id) < 8:
        return None
    events_file = Path.home() / ".copilot" / "session-state" / session_id / "events.jsonl"
    if not events_file.is_file():
        return None
    try:
        if events_file.stat().st_size > 32 * 1024 * 1024:
            return None
        turn_tokens = 0
        prev_inp = 0
        saw_usage = False
        with events_file.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if '"user.message"' in line:
                    try:
                        ev = json.loads(line)
                        if ev.get("type") == "user.message":
                            turn_tokens = 0
                            saw_usage = False
                    except ValueError:
                        continue
                elif '"session.usage_record"' in line:
                    try:
                        ev = json.loads(line)
                        if ev.get("type") == "session.usage_record":
                            usage = ev.get("data", {}).get("usage", {})
                            inp = int(usage.get("inputTokens", 0) or 0)
                            cached = int(usage.get("cacheReadTokens", 0) or 0)
                            out = int(usage.get("outputTokens", 0) or 0)
                            step_delta = max(0, inp - prev_inp) if (prev_inp > 0 and inp >= prev_inp) else min(max(0, inp - cached), 24_000)
                            non_cached = max(0, inp - cached) if cached > 0 else step_delta
                            incremental = min(non_cached, step_delta) + max(0, out)
                            prev_inp = inp
                            turn_tokens += incremental
                            saw_usage = True
                    except (TypeError, ValueError):
                        continue
        if saw_usage and 200 <= turn_tokens <= 120_000:
            return turn_tokens
    except OSError:
        return None
    return None


def record_token_baseline(store: Store, workspace: str, kind: str, subject: str,
                          acquisition_tokens: int, injected_tokens: Optional[int] = None,
                          basis: str = "measured", detail: str = "") -> Dict[str, Any]:
    require(kind in ("memo", "claim", "solution"), "Token baseline kind must be memo, claim, or solution")
    require(isinstance(acquisition_tokens, int) and 1 <= acquisition_tokens <= 10_000_000,
            "acquisition_tokens must be between 1 and 10000000")
    store.workspace(workspace)
    inj = int(injected_tokens) if injected_tokens is not None and injected_tokens >= 0 else 180
    saved = max(0, acquisition_tokens - inj)
    ts = now()
    with store.transaction():
        store.db.execute(
            "INSERT INTO token_baselines VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(workspace,kind,subject) DO UPDATE SET "
            "acquisition_tokens=excluded.acquisition_tokens,"
            "reusable_saved_tokens=excluded.reusable_saved_tokens,"
            "injected_tokens=excluded.injected_tokens,"
            "basis=excluded.basis,detail=excluded.detail,updated_at=excluded.updated_at",
            (workspace, kind, subject, acquisition_tokens, saved, inj, basis[:32], detail[:240], ts),
        )
    return {
        "workspace": workspace,
        "kind": kind,
        "subject": subject,
        "acquisition_tokens": acquisition_tokens,
        "injected_tokens": inj,
        "reusable_saved_tokens": saved,
        "basis": basis,
    }


def _workspace_roots(store: Store) -> Dict[str, Path]:
    roots: Dict[str, Path] = {}
    for row in store.db.execute("SELECT id,root FROM workspaces"):
        roots[row["id"]] = Path(row["root"]).resolve()
    return roots


def _anchor_file_tokens(ws_root: Optional[Path], rel_path: str,
                        cache: Dict[Tuple[str, str], int]) -> int:
    key = (str(ws_root or ""), rel_path)
    if key in cache:
        return cache[key]
    tok = 1_400  # Conservative fallback if checkout moved or file missing
    if ws_root is not None and rel_path:
        try:
            f = workspace_file(ws_root, rel_path)
            if f.is_file():
                capped_bytes = min(f.stat().st_size, MAX_SOURCE_FILE_INSPECTION_BYTES)
                tok = max(350, capped_bytes // 4)
        except (RuntimeErrorBase, OSError):
            pass
    cache[key] = tok
    return tok


def _evidence_tokens(store: Store, evidence_ids: Iterable[str],
                     cache: Dict[str, int]) -> int:
    total = 0
    for eid in evidence_ids:
        if not eid:
            continue
        if eid not in cache:
            tok = 1_200
            row = store.db.execute("SELECT media_type,sha256 FROM evidence WHERE id=?", (eid,)).fetchone()
            if row:
                blob = store.blobs / row["sha256"]
                if blob.is_file():
                    sz = blob.stat().st_size
                    if row["media_type"] == "image/png":
                        tok = 1_600  # Vision screenshot inspection + ADB capture roundtrip
                    else:
                        tok = max(450, min(sz, 24_000) // 4)
            cache[eid] = tok
        total += cache[eid]
    return total


def build_token_profiles(store: Store) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Build deterministic token profiles keyed by (kind, subject) across all workspaces."""
    ws_roots = _workspace_roots(store)
    file_tok_cache: Dict[Tuple[str, str], int] = {}
    ev_tok_cache: Dict[str, int] = {}

    explicit_map: Dict[Tuple[str, str, str], sqlite3.Row] = {}
    for row in store.db.execute("SELECT * FROM token_baselines"):
        explicit_map[(row["workspace"], row["kind"], row["subject"])] = row

    basis_labels = {
        "measured": "实测记录",
        "turn_telemetry": "会话实测",
        "source_anchors": "源码锚点实算",
        "runtime_evidence": "验证证据实算",
        "contract_steps": "零模型契约实算",
    }

    profiles: Dict[Tuple[str, str], Dict[str, Any]] = {}

    # 1) Memos
    for row in store.db.execute("SELECT workspace,id,body FROM memos"):
        ws = row["workspace"]
        mid = row["id"]
        try:
            spec = json.loads(row["body"])
        except ValueError:
            continue
        injected_text = " ".join([
            str(spec.get("title", "")),
            str(spec.get("when", "")),
            str(spec.get("rule", "")),
            str(spec.get("trap", "")),
        ])
        inj_tok = max(60, estimate_text_tokens(injected_text))
        anchors = spec.get("verification", {}).get("anchors", [])
        anchor_paths = [str(a.get("path", "")) for a in anchors if a.get("path")]

        ws_root = ws_roots.get(ws)
        anchor_tok_map = {p: _anchor_file_tokens(ws_root, p, file_tok_cache) for p in anchor_paths}
        exp = explicit_map.get((ws, "memo", mid))
        if exp is not None:
            acq_tok = int(exp["acquisition_tokens"])
            inj_tok = int(exp["injected_tokens"])
            basis = str(exp["basis"])
        elif isinstance(spec.get("acquisition_tokens"), int) and spec["acquisition_tokens"] > 0:
            acq_tok = int(spec["acquisition_tokens"])
            basis = "measured"
        else:
            files_tok = sum(anchor_tok_map.values())
            roundtrips = (len(anchor_paths) + 1) * TOOL_ROUNDTRIP_OVERHEAD_TOKENS
            acq_tok = max(1_800, files_tok + roundtrips)
            basis = "source_anchors"

        saved_hit = max(0, acq_tok - inj_tok)
        profiles[("memo", mid)] = {
            "workspace": ws,
            "kind": "memo",
            "subject": mid,
            "acquisition_tokens": acq_tok,
            "injected_tokens": inj_tok,
            "saved_per_hit": saved_hit,
            "basis": basis,
            "basis_label": basis_labels.get(basis, "源码锚点实算"),
            "anchor_paths": set(anchor_paths),
            "anchor_tok_map": anchor_tok_map,
        }

    # 2) Claims
    for row in store.db.execute("SELECT id,body FROM claims"):
        cid = row["id"]
        try:
            body = json.loads(row["body"])
            spec = body["spec"]
        except (KeyError, ValueError):
            continue
        ws = spec["workspace"]
        prov = spec.get("provenance", {})
        injected_text = str(prov.get("title", "")) + " " + str(spec.get("statement", ""))
        inj_tok = max(45, estimate_text_tokens(injected_text))
        anchors = spec.get("anchors", [])
        anchor_paths = [str(a.get("path", "")) for a in anchors if a.get("path")]
        ev_ids = [str(c.get("evidence_id", "")) for c in spec.get("checks", []) if c.get("evidence_id")]

        ws_root = ws_roots.get(ws)
        anchor_tok_map = {p: _anchor_file_tokens(ws_root, p, file_tok_cache) for p in anchor_paths}
        exp = explicit_map.get((ws, "claim", cid))
        if exp is not None:
            acq_tok = int(exp["acquisition_tokens"])
            inj_tok = int(exp["injected_tokens"])
            basis = str(exp["basis"])
        elif isinstance(prov.get("acquisition_tokens"), int) and prov["acquisition_tokens"] > 0:
            acq_tok = int(prov["acquisition_tokens"])
            basis = "measured"
        elif anchor_paths:
            files_tok = sum(anchor_tok_map.values())
            roundtrips = (len(anchor_paths) + 1) * TOOL_ROUNDTRIP_OVERHEAD_TOKENS
            acq_tok = max(1_600, files_tok + roundtrips)
            basis = "source_anchors"
        else:
            ev_tok = _evidence_tokens(store, set(ev_ids), ev_tok_cache)
            acq_tok = max(1_800, ev_tok + 2 * TOOL_ROUNDTRIP_OVERHEAD_TOKENS)
            basis = "runtime_evidence"

        saved_hit = max(0, acq_tok - inj_tok)
        profiles[("claim", cid)] = {
            "workspace": ws,
            "kind": "claim",
            "subject": cid,
            "acquisition_tokens": acq_tok,
            "injected_tokens": inj_tok,
            "saved_per_hit": saved_hit,
            "basis": basis,
            "basis_label": basis_labels.get(basis, "验证证据实算"),
            "anchor_paths": set(anchor_paths),
            "anchor_tok_map": anchor_tok_map,
        }

    # 3) Solutions (Zero-model P/R/Q contracts)
    for row in store.db.execute("SELECT workspace,id,body FROM solutions"):
        ws = row["workspace"]
        sid = row["id"]
        try:
            spec = json.loads(row["body"])
        except ValueError:
            continue
        step_paths = [
            str(st.get("path")) for st in spec.get("steps", [])
            if isinstance(st.get("path"), str)
        ] + [
            str(pre.get("path")) for pre in spec.get("preconditions", [])
            if pre.get("kind") == "file_sha256" and isinstance(pre.get("path"), str)
        ]
        unique_paths = list(dict.fromkeys(step_paths))

        ws_root = ws_roots.get(ws)
        anchor_tok_map = {p: _anchor_file_tokens(ws_root, p, file_tok_cache) for p in unique_paths}
        exp = explicit_map.get((ws, "solution", sid))
        if exp is not None:
            acq_tok = int(exp["acquisition_tokens"])
            inj_tok = int(exp["injected_tokens"])
            basis = str(exp["basis"])
        else:
            files_tok = sum(anchor_tok_map.values())
            # Zero-model execution avoids an entire LLM prompt+reasoning turn (~2,400 tokens) + file reads
            acq_tok = max(2_600, files_tok + 2_400)
            inj_tok = 0
            basis = "contract_steps"

        saved_hit = max(0, acq_tok - inj_tok)
        profiles[("solution", sid)] = {
            "workspace": ws,
            "kind": "solution",
            "subject": sid,
            "acquisition_tokens": acq_tok,
            "injected_tokens": inj_tok,
            "saved_per_hit": saved_hit,
            "basis": basis,
            "basis_label": basis_labels.get(basis, "零模型契约实算"),
            "anchor_paths": set(unique_paths),
            "anchor_tok_map": anchor_tok_map,
        }

    return profiles


def fallback_profile(kind: str, subject: str, workspace: str = "") -> Dict[str, Any]:
    acq = 2_400 if kind != "solution" else 3_000
    inj = 150 if kind != "solution" else 0
    return {
        "workspace": workspace,
        "kind": kind,
        "subject": subject,
        "acquisition_tokens": acq,
        "injected_tokens": inj,
        "saved_per_hit": acq - inj,
        "basis": "runtime_evidence",
        "basis_label": "验证基线估算",
        "anchor_paths": set(),
        "anchor_tok_map": {},
    }


def evaluate_hit_events(store: Store,
                        profiles: Dict[Tuple[str, str], Dict[str, Any]],
                        day_ago_iso: str) -> Dict[str, Any]:
    """
    Evaluate every hit event in memory_events with shared-anchor deduplication per retrieval batch.
    Returns per-event saved_tokens map, per-item cumulative savings, per-session savings, and Gas Gauge summary.
    """
    rows = store.db.execute(
        "SELECT id,workspace,kind,subject,event,session,source,occurred_at "
        "FROM memory_events WHERE event IN ('retrieved','expanded','selected','executed') "
        "AND session != 'bundle-import' ORDER BY occurred_at ASC"
    ).fetchall()

    # Track seen anchor files and orientation within (session, minute_bucket) so paired Memo+Claim
    # hits in a single search call never double-count the same anchored source files.
    batch_seen_anchors: Dict[Tuple[str, str], Set[str]] = {}
    batch_seen_l0: Set[Tuple[str, str]] = set()

    event_savings: Dict[str, Dict[str, int]] = {}
    item_saved_totals: Dict[Tuple[str, str], int] = {}
    session_saved_totals: Dict[str, int] = {}
    session_item_saved: Dict[Tuple[str, str, str], int] = {}
    reused_items: Set[Tuple[str, str]] = set()

    total_saved = 0
    total_injected = 0
    today_saved = 0
    today_injected = 0

    for row in rows:
        ev_id = row["id"]
        kind = row["kind"]
        subj = row["subject"]
        sid = row["session"] or ""
        occ = str(row["occurred_at"] or "")
        minute_key = (sid, occ[:16])
        prof = profiles.get((kind, subj)) or fallback_profile(kind, subj, row["workspace"])

        source_prefix = str(row["source"] or "").split(":", 1)[0].strip().lower()
        ev_name = row["event"]
        anchors: Set[str] = prof["anchor_paths"]
        anchor_tok_map: Dict[str, int] = prof.get("anchor_tok_map", {})

        if ev_name == "executed" or source_prefix == "execute":
            inj = 0
            saved = prof["acquisition_tokens"]
        elif source_prefix == "session_index":
            inj = 35
            if minute_key in batch_seen_l0:
                saved = 180
            else:
                batch_seen_l0.add(minute_key)
                saved = min(prof["saved_per_hit"], SESSION_INDEX_ORIENTATION_TOKENS)
        else:
            inj = prof["injected_tokens"]
            seen_in_batch = batch_seen_anchors.setdefault(minute_key, set())
            if anchors:
                unseen = anchors - seen_in_batch
                if not unseen:
                    saved = max(200, min(prof["saved_per_hit"], SECONDARY_SHARED_ANCHOR_SAVINGS) - inj)
                elif len(unseen) == len(anchors):
                    saved = prof["saved_per_hit"]
                else:
                    unseen_files_tok = sum(anchor_tok_map.get(p, 1_400) for p in unseen)
                    marginal_acq = unseen_files_tok + len(unseen) * TOOL_ROUNDTRIP_OVERHEAD_TOKENS
                    saved = max(250, min(prof["saved_per_hit"], marginal_acq - inj))
                seen_in_batch.update(anchors)
            else:
                saved = prof["saved_per_hit"]

        event_savings[ev_id] = {
            "saved_tokens": saved,
            "injected_tokens": inj,
            "acquisition_tokens": prof["acquisition_tokens"],
        }
        item_key = (kind, subj)
        reused_items.add(item_key)
        item_saved_totals[item_key] = item_saved_totals.get(item_key, 0) + saved
        if sid:
            session_saved_totals[sid] = session_saved_totals.get(sid, 0) + saved
            sk = (sid, kind, subj)
            session_item_saved[sk] = session_item_saved.get(sk, 0) + saved

        total_saved += saved
        total_injected += inj
        if occ >= day_ago_iso:
            today_saved += saved
            today_injected += inj

    reused_acquisition_sum = sum(
        (profiles.get(k) or fallback_profile(k[0], k[1]))["acquisition_tokens"]
        for k in reused_items
    )
    all_acquisition_sum = sum(p["acquisition_tokens"] for p in profiles.values())
    denom = reused_acquisition_sum if reused_acquisition_sum > 0 else max(all_acquisition_sum, 1)
    roi_multiple = round(total_saved / max(denom, 1), 1) if total_saved > 0 else 0.0

    next_milestone = GAS_GAUGE_MILESTONES[-1]
    for m in GAS_GAUGE_MILESTONES:
        if total_saved < m:
            next_milestone = m
            break
    else:
        next_milestone = max(total_saved * 2, GAS_GAUGE_MILESTONES[-1])

    fill_ratio = min(1.0, max(0.06 if total_saved > 0 else 0.0, total_saved / max(next_milestone, 1)))

    return {
        "event_savings": event_savings,
        "item_saved_totals": item_saved_totals,
        "session_saved_totals": session_saved_totals,
        "session_item_saved": session_item_saved,
        "gas_gauge": {
            "total_saved_tokens": total_saved,
            "total_saved_formatted": format_tokens(total_saved),
            "today_saved_tokens": today_saved,
            "today_saved_formatted": format_tokens(today_saved),
            "total_injected_tokens": total_injected,
            "today_injected_tokens": today_injected,
            "reused_acquisition_tokens": reused_acquisition_sum,
            "reused_acquisition_formatted": format_tokens(reused_acquisition_sum),
            "roi_multiple": roi_multiple,
            "fill_ratio": round(fill_ratio, 3),
            "next_milestone_tokens": next_milestone,
            "next_milestone_formatted": format_tokens(next_milestone),
        },
    }
