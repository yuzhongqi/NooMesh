"""Deterministic offline consolidation ('dream-pass'): anchor repair, theme hygiene, and turnaround digests."""

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .context import DERIVED_THEME_IDS, _theme_anchor_bytes, build_view
from .core import (MAX_PACKET_BYTES, RuntimeErrorBase, Store, canonical, digest,
                   file_digest, now, require, workspace_file)
from .lifecycle import SOURCE_PROVENANCE_KINDS, assess, parsed, record as record_usage
from .memos import verify_memo_spec


def _collect_turnarounds(store: Store, workspace: str, limit: int = 8) -> List[Dict[str, Any]]:
    episodes: List[Dict[str, Any]] = []
    tasks = store.db.execute(
        "SELECT id,title,state,updated_at FROM task_runs WHERE workspace=? "
        "ORDER BY updated_at DESC LIMIT 64", (workspace,)).fetchall()
    for task in tasks:
        traces = store.db.execute(
            "SELECT tool,result_type,detail,created_at FROM tool_traces "
            "WHERE task_run=? ORDER BY created_at ASC, rowid ASC", (task["id"],)).fetchall()
        first_fail_idx = next((i for i, row in enumerate(traces)
                               if row["result_type"] == "failure"), None)
        if first_fail_idx is None:
            continue
        recovery = [row["tool"] for row in traces[first_fail_idx + 1:]
                    if row["result_type"] == "success"]
        if not recovery and task["state"] != "PASS":
            continue
        first_fail = traces[first_fail_idx]
        episodes.append({
            "task_run": task["id"],
            "title": task["title"][:120],
            "failed_tool": first_fail["tool"],
            "error_signature": first_fail["detail"][:200],
            "recovered_via": list(dict.fromkeys(recovery))[:3],
        })
        if len(episodes) >= limit:
            break
    return episodes


def dream_pass(store: Store, workspace: str, apply: bool = False,
               approve: bool = False) -> Dict[str, Any]:
    require(not apply or approve,
            "Explicit approval required to apply offline dream consolidation")
    scope = store.workspace(workspace)
    root = Path(scope["root"])
    reanchorable: List[Dict[str, Any]] = []
    reanchored: List[Dict[str, Any]] = []
    broken_anchors: List[Dict[str, Any]] = []

    for row in store.db.execute(
            "SELECT * FROM memos WHERE workspace=? AND lifecycle='active' ORDER BY id",
            (workspace,)).fetchall():
        spec = json.loads(row["body"])
        try:
            ver = verify_memo_spec(store, spec, require_exact=False)
        except (RuntimeErrorBase, OSError, ValueError) as exc:
            broken_anchors.append({"kind": "memo", "id": row["id"], "reason": str(exc)})
            continue
        health = assess(store, workspace, "memo", row["id"])
        needs_refresh = bool(ver["anchor_drift"]) or health["review_due"] or health["status"] == "EXPIRED"
        if not needs_refresh:
            continue
        reanchorable.append({"kind": "memo", "id": row["id"],
                             "drifted_files": ver["anchor_drift"],
                             "memory_status": health["status"]})
        if apply:
            updated_spec = copy.deepcopy(spec)
            for anchor in updated_spec["verification"]["anchors"]:
                anchor["sha256"] = file_digest(workspace_file(root, anchor["path"]))
            verify_memo_spec(store, updated_spec, require_exact=True)
            body = canonical(updated_spec)
            fingerprint = digest(body.encode())
            ts = now()
            version = row["version"] + 1
            with store.transaction():
                current = store.db.execute(
                    "SELECT version FROM memos WHERE workspace=? AND id=?",
                    (workspace, row["id"])).fetchone()
                require(current is not None and current["version"] == row["version"],
                        "Concurrent memo update during dream-pass")
                store.db.execute(
                    "UPDATE memos SET version=?,body=?,fingerprint=?,updated_at=? "
                    "WHERE workspace=? AND id=?",
                    (version, body, fingerprint, ts, workspace, row["id"]))
                store.db.execute("INSERT INTO memo_versions VALUES (?,?,?,?,?)",
                                 (workspace, row["id"], version, body, fingerprint))
                record_usage(store, workspace, "memo", row["id"], version,
                             "verified_success", "dream-pass",
                             "dream-memo:" + fingerprint[:24], occurred_at=ts)
            reanchored.append({"kind": "memo", "id": row["id"], "version": version})

    for claim_id, claim in sorted(store.snapshot().items()):
        if claim["spec"]["workspace"] != workspace or claim.get("lifecycle") != "active":
            continue
        spec = claim["spec"]
        prov_kind = spec.get("provenance", {}).get("kind")
        has_symbols = any("symbols" in a for a in spec["anchors"])
        if prov_kind not in SOURCE_PROVENANCE_KINDS and not has_symbols:
            continue
        conflict = store.db.execute(
            "SELECT 1 FROM knowledge_relations r JOIN milestones m ON m.id=r.target "
            "WHERE m.claim_id=? AND r.relation IN ('contradicts','supersedes') LIMIT 1",
            (claim_id,)).fetchone()
        if conflict:
            broken_anchors.append({"kind": "claim", "id": claim_id,
                                   "reason": "Conflicting/superseding candidate requires reconciliation"})
            continue
        try:
            ver = store.verify(spec, require_exact_anchors=False)
        except (RuntimeErrorBase, OSError, ValueError) as exc:
            broken_anchors.append({"kind": "claim", "id": claim_id, "reason": str(exc)})
            continue
        if ver["verdict"] != "PASS":
            broken_anchors.append({"kind": "claim", "id": claim_id, "reason": "Evidence check failed"})
            continue
        health = assess(store, workspace, "claim", claim_id)
        needs_refresh = bool(ver["anchor_drift"]) or (
            prov_kind in SOURCE_PROVENANCE_KINDS
            and (health["review_due"] or health["status"] == "EXPIRED"))
        if not needs_refresh:
            continue
        reanchorable.append({"kind": "claim", "id": claim_id,
                             "drifted_files": ver["anchor_drift"],
                             "memory_status": health["status"]})
        if apply:
            updated_spec = copy.deepcopy(spec)
            for anchor in updated_spec["anchors"]:
                anchor["sha256"] = file_digest(workspace_file(root, anchor["path"]))
            ts = now()
            if isinstance(updated_spec.get("provenance"), dict):
                updated_spec["provenance"] = dict(updated_spec["provenance"], validated_at=ts)
            cand = store.propose(updated_spec)
            current = store.snapshot().get(claim_id)
            require(current is not None and current["version"] == claim["version"],
                    "Concurrent claim update during dream-pass")
            published = store.accept(cand["candidate_id"], current["version"], store.revision(), True)
            record_usage(store, workspace, "claim", claim_id, published["version"],
                         "verified_success", "dream-pass",
                         "dream-claim:" + published["revision"][:24], occurred_at=ts)
            reanchored.append({"kind": "claim", "id": claim_id, "version": published["version"]})

    view = build_view(store)
    active_nodes = {k for k, v in view["nodes"].items()
                    if v["workspace"] == workspace
                    and v["status"] in ("active", "verified", "UNVERIFIED_LEGACY")}
    theme_repairs: List[Dict[str, Any]] = []
    bloat_warnings: List[Dict[str, Any]] = []
    covered_nodes = set()

    for row in store.db.execute("SELECT id,body FROM themes WHERE workspace=? ORDER BY id",
                                (workspace,)).fetchall():
        if row["id"] in DERIVED_THEME_IDS:
            continue
        theme = json.loads(row["body"])
        new_members: List[str] = []
        dangling: List[str] = []
        remapped: List[Dict[str, str]] = []
        for member in theme["members"]:
            if member in active_nodes:
                new_members.append(member)
            elif member.startswith("claim:"):
                replacement = "memo:" + workspace + ":" + member.split(":", 1)[1]
                if replacement in active_nodes:
                    new_members.append(replacement)
                    remapped.append({"from": member, "to": replacement})
                else:
                    dangling.append(member)
            else:
                dangling.append(member)
        new_members = list(dict.fromkeys(new_members))
        if new_members != theme["members"]:
            repair = {"theme": row["id"], "dangling": dangling,
                      "remapped": remapped, "applied": apply}
            theme_repairs.append(repair)
            if apply:
                updated_theme = dict(theme, members=new_members,
                                     version=theme.get("version", 1) + 1)
                with store.transaction():
                    store.db.execute("UPDATE themes SET body=? WHERE workspace=? AND id=?",
                                     (canonical(updated_theme), workspace, row["id"]))
                theme = updated_theme
        covered_nodes.update(new_members)
        source_bytes = _theme_anchor_bytes(store, workspace, new_members)
        theme_bytes = len(canonical(theme).encode())
        if source_bytes > 0 and theme_bytes > source_bytes:
            bloat_warnings.append({"theme": row["id"], "theme_bytes": theme_bytes,
                                   "anchored_source_bytes": source_bytes})

    if apply and (reanchored or theme_repairs):
        view = build_view(store)
    unmapped = sorted(
        k for k, v in view["nodes"].items()
        if v["workspace"] == workspace and v["kind"] in ("solution", "memo", "claim")
        and v["status"] in ("active", "verified") and k not in covered_nodes
    )
    turnarounds = _collect_turnarounds(store, workspace)
    result: Dict[str, Any] = {
        "schema": "llr.dream.v1",
        "workspace": workspace,
        "mode": "APPLIED" if apply else "DRY_RUN",
        "revision": view["revision"],
        "reanchorable": reanchorable[:24],
        "reanchored": reanchored[:24],
        "broken_anchors": broken_anchors[:24],
        "theme_repairs": theme_repairs[:16],
        "bloat_warnings": bloat_warnings[:16],
        "unmapped_verified_count": len(unmapped),
        "unmapped_verified_sample": unmapped[:12],
        "turnaround_episodes": turnarounds,
        "model_calls": 0,
        "notice": "Deterministic offline consolidation only; runtime claims without symbol anchors "
                  "still require approved App revalidation.",
    }
    while len(canonical(result).encode()) > MAX_PACKET_BYTES and result["turnaround_episodes"]:
        result["turnaround_episodes"].pop()
    while len(canonical(result).encode()) > MAX_PACKET_BYTES and result["broken_anchors"]:
        result["broken_anchors"].pop()
    return result


def run_idle_dreams(store: Store) -> List[Dict[str, Any]]:
    policy_path = store.home / "automation.json"
    if not policy_path.is_file():
        return []
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if policy.get("local_only") is not True or policy.get("auto_dream_source") is not True:
        return []
    interval = policy.get("dream_interval_seconds", 43200)
    if type(interval) is not int or interval < 60:
        interval = 43200
    clock = parsed(now())
    completed: List[Dict[str, Any]] = []
    for workspace in policy.get("workspaces", []):
        row = store.db.execute("SELECT root FROM workspaces WHERE id=?", (workspace,)).fetchone()
        if row is None or not Path(row["root"]).is_dir():
            continue
        if store.db.execute("SELECT 1 FROM missions WHERE workspace=? AND state='RUNNING' LIMIT 1",
                            (workspace,)).fetchone():
            continue
        if store.db.execute("SELECT 1 FROM solution_runs WHERE workspace=? AND state='RUNNING' LIMIT 1",
                            (workspace,)).fetchone():
            continue
        state_key = "last_dream:" + workspace
        last_row = store.db.execute("SELECT value FROM state WHERE key=?", (state_key,)).fetchone()
        last_ts = parsed(last_row["value"]) if last_row else None
        if last_ts and clock and (clock - last_ts).total_seconds() < interval:
            continue
        report = dream_pass(store, workspace, apply=True, approve=True)
        ts = now()
        receipt_body = {
            "state_home": str(store.home),
            "workspace": workspace,
            "event": "autoDreamPass",
            "host_pid": os.getpid(),
            "reanchored": len(report["reanchored"]),
            "theme_repairs": len(report["theme_repairs"]),
            "broken_anchors": len(report["broken_anchors"]),
            "model_calls": 0,
        }
        receipt_id = digest(canonical(["host-idle", workspace, ts, receipt_body]).encode())
        with store.transaction():
            store.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (state_key, ts))
            store.db.execute("INSERT OR IGNORE INTO client_receipts VALUES (?,?,?,?,?,?,?)",
                             (receipt_id, "host-idle", "host-idle", "autoDreamPass",
                              workspace, canonical(receipt_body), ts))
        completed.append(receipt_body)
    return completed
