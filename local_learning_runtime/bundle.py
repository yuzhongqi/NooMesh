"""Portable .noomesh doctrine bundles: AirDrop-ready export, lineage merge, and local re-attestation."""

import io
import json
import re
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .core import (MAX_EVIDENCE_BYTES, RuntimeErrorBase, SHA256, Store, canonical,
                   check_evidence, conditions as validate_conditions, digest, file_digest,
                   identifier, now, object_fields, require, validate_anchor, verify_anchor)
from .learning import privacy_key
from .lifecycle import SOURCE_PROVENANCE_KINDS, record as record_usage
from .memos import validate_memo
from .notifier import default_machine_name
from .privacy import sanitize
from .solutions import all_steps, check_preconditions, validate_spec as validate_solution_spec
from .tokens import build_token_profiles, record_token_baseline

BUNDLE_SCHEMA = "noomesh.bundle.v1"
ITEM_SCHEMA = "noomesh.doctrine_item.v1"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024

ABSOLUTE_PATH_LEAK = re.compile(r"(/(?:Users|Volumes|home|private/var)/[^\s\"']+)")


def _assert_no_local_paths(value: Any, label: str) -> None:
    dumped = canonical(value)
    match = ABSOLUTE_PATH_LEAK.search(dumped)
    require(match is None, f"{label} contains machine-local filesystem path: {match.group(1) if match else ''}")


def _rewrite_workspace_spec(kind: str, spec: Dict[str, Any], target_workspace: str) -> Dict[str, Any]:
    rewritten = json.loads(canonical(spec))
    rewritten["workspace"] = target_workspace
    return rewritten


def _portable_fingerprint(kind: str, spec: Dict[str, Any]) -> str:
    # Normalize workspace to a neutral placeholder when computing cross-workspace lineage hashes
    # so mapping 'app/android' -> 'app/android' or remapping to another workspace ID preserves lineage.
    normalized = json.loads(canonical(spec))
    normalized["workspace"] = "__portable__"
    return digest(canonical(normalized).encode())


def _safe_tar_members(tf: tarfile.TarFile) -> Dict[str, bytes]:
    members = tf.getmembers()
    require(1 <= len(members) <= 2048, "Bundle archive member count out of bounds")
    extracted: Dict[str, bytes] = {}
    total_size = 0
    for member in members:
        require(member.isfile(), f"Unsupported archive entry type (links/dirs disallowed): {member.name}")
        name = member.name.lstrip("./")
        parts = Path(name).parts
        require(
            bool(name) and not Path(name).is_absolute() and ".." not in parts and "\\" not in name,
            f"Unsafe path inside bundle archive: {member.name}",
        )
        require(0 <= member.size <= MAX_MEMBER_BYTES, f"Bundle entry exceeds size limit: {name}")
        total_size += member.size
        require(total_size <= MAX_BUNDLE_BYTES, "Unpacked bundle exceeds 64 MiB limit")
        stream = tf.extractfile(member)
        require(stream is not None, f"Readable content missing for {name}")
        raw = stream.read(MAX_MEMBER_BYTES + 1)
        require(len(raw) == member.size, f"Truncated or oversized entry {name}")
        extracted[name] = raw
    return extracted


def _write_deterministic_tar_gz(dest: Path, files_map: Dict[str, bytes]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
    with tarfile.open(tmp_dest, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        for rel_name in sorted(files_map.keys()):
            raw = files_map[rel_name]
            info = tarfile.TarInfo(name=rel_name)
            info.size = len(raw)
            info.mtime = 0
            info.mode = 0o600
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tf.addfile(info, io.BytesIO(raw))
    tmp_dest.replace(dest)


def _select_export_workspace(store: Store, workspace: Optional[str]) -> str:
    if workspace:
        store.workspace(workspace)
        return workspace
    from .menubar import _select_primary_workspace
    chosen = _select_primary_workspace(store)
    require(bool(chosen), "No registered workspace available to export")
    return str(chosen)


def default_bundle_output_path(store: Store, workspace: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = workspace.replace("/", "-")
    out_dir = store.home / "bundles"
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return out_dir / f"{slug}-{stamp}.noomesh"


def export_bundle(store: Store, workspace: Optional[str] = None,
                  output: Optional[Path] = None,
                  include_runtime_claims: bool = False,
                  machine_name: Optional[str] = None,
                  approve: bool = False) -> Dict[str, Any]:
    require(approve, "Explicit approval required to export a portable .noomesh doctrine bundle")
    ws = _select_export_workspace(store, workspace)
    ws_row = store.workspace(ws)
    platform = str(ws_row["platform"])
    origin = (machine_name or default_machine_name()).strip()[:120]
    exported_at = now()
    out_path = (output.expanduser().resolve() if output is not None
                else default_bundle_output_path(store, ws))
    if out_path.suffix.lower() != ".noomesh":
        out_path = out_path.with_name(out_path.name + ".noomesh")

    key = privacy_key(store)
    tok_profiles = build_token_profiles(store)

    # 1) Active Memos + lineage chain
    memos_out: List[Dict[str, Any]] = []
    for row in store.db.execute(
        "SELECT * FROM memos WHERE workspace=? AND lifecycle='active' ORDER BY id", (ws,)
    ):
        spec = json.loads(row["body"])
        safe_spec, redactions = sanitize(spec, key)
        require(not redactions, f"Memo {row['id']} contains unredacted sensitive data")
        _assert_no_local_paths(safe_spec, f"Memo {row['id']}")

        history_rows = store.db.execute(
            "SELECT version, body FROM memo_versions WHERE workspace=? AND id=? ORDER BY version ASC",
            (ws, row["id"]),
        ).fetchall()
        lineage_chain = [_portable_fingerprint("memo", json.loads(h["body"])) for h in history_rows]
        cur_sha = _portable_fingerprint("memo", safe_spec)
        if not lineage_chain or lineage_chain[-1] != cur_sha:
            lineage_chain.append(cur_sha)
        parent_sha = lineage_chain[-2] if len(lineage_chain) >= 2 else None

        prof = tok_profiles.get(("memo", row["id"]), {})
        acq_tok = int(prof.get("acquisition_tokens", 2400))
        inj_tok = int(prof.get("injected_tokens", 150))
        basis = str(prof.get("basis", "source_anchors"))

        memos_out.append({
            "schema": ITEM_SCHEMA,
            "kind": "memo",
            "id": row["id"],
            "workspace": ws,
            "platform": platform,
            "lineage": {
                "content_sha256": cur_sha,
                "parent_sha256": parent_sha,
                "ancestors": lineage_chain[:-1][-16:],
                "origin_machine": origin,
                "updated_at": row["updated_at"],
            },
            "economics": {
                "acquisition_tokens": acq_tok,
                "injected_tokens": inj_tok,
                "basis": basis,
            },
            "human_layer": {
                "title": safe_spec["title"],
                "memo_kind": safe_spec["kind"],
                "tags": safe_spec.get("tags", []),
            },
            "spec": safe_spec,
        })

    # 2) Portable Claims + referenced evidence blobs
    # By default export source-anchored / architectural claims; runtime step-by-step UI claims are opt-in.
    snapshot = store.snapshot()
    claims_out: List[Dict[str, Any]] = []
    evidence_meta: Dict[str, Dict[str, Any]] = {}
    evidence_blobs: Dict[str, bytes] = {}
    exported_claim_ids: Set[str] = set()

    for claim_id in sorted(snapshot.keys()):
        claim_obj = snapshot[claim_id]
        if claim_obj.get("lifecycle") != "active":
            continue
        spec = claim_obj["spec"]
        if spec["workspace"] != ws:
            continue
        prov_kind = spec.get("provenance", {}).get("kind")
        is_source_claim = bool(spec.get("anchors")) or prov_kind in SOURCE_PROVENANCE_KINDS
        if not include_runtime_claims and not is_source_claim:
            continue

        safe_spec, redactions = sanitize(spec, key)
        require(not redactions, f"Claim {claim_id} contains unredacted sensitive data")
        _assert_no_local_paths(safe_spec, f"Claim {claim_id}")

        # Collect every referenced evidence blob
        ev_records = []
        for chk in safe_spec["checks"]:
            eid = chk["evidence_id"]
            ev_row, raw_bytes = store.evidence(eid)
            if ev_row["media_type"] == "application/json":
                parsed_ev = json.loads(raw_bytes)
                _, ev_redactions = sanitize(parsed_ev, key)
                require(not ev_redactions, f"Evidence {eid[:12]} for claim {claim_id} contains sensitive data")
                _assert_no_local_paths(parsed_ev, f"Evidence {eid[:12]}")
            sha = ev_row["sha256"]
            evidence_blobs[sha] = raw_bytes
            ev_info = {
                "evidence_id": eid,
                "event_id": ev_row["event_id"],
                "media_type": ev_row["media_type"],
                "sha256": sha,
                "conditions": json.loads(ev_row["conditions"]),
                "created_at": ev_row["created_at"],
            }
            evidence_meta[eid] = ev_info
            ev_records.append(ev_info)

        cur_sha = _portable_fingerprint("claim", safe_spec)
        prof = tok_profiles.get(("claim", claim_id), {})
        prov_title = safe_spec.get("provenance", {}).get("title") or safe_spec["statement"][:120]
        claims_out.append({
            "schema": ITEM_SCHEMA,
            "kind": "claim",
            "id": claim_id,
            "workspace": ws,
            "platform": platform,
            "lineage": {
                "content_sha256": cur_sha,
                "parent_sha256": None,
                "ancestors": [],
                "origin_machine": origin,
                "updated_at": safe_spec.get("provenance", {}).get("validated_at") or exported_at,
            },
            "economics": {
                "acquisition_tokens": int(prof.get("acquisition_tokens", 2400)),
                "injected_tokens": int(prof.get("injected_tokens", 140)),
                "basis": str(prof.get("basis", "source_anchors")),
            },
            "human_layer": {
                "title": str(prov_title),
                "task_type": safe_spec["task_type"],
            },
            "spec": safe_spec,
            "dependency_versions": claim_obj.get("dependency_versions", {}),
            "evidence": ev_records,
        })
        exported_claim_ids.add(claim_id)

    # Filter out claims whose dependencies were excluded from this export
    claims_out = [
        c for c in claims_out
        if set(c["spec"].get("dependencies", [])) <= exported_claim_ids
    ]

    # 3) Solutions (Verified & Candidate P/R/Q contracts + verified parameter cases for pure file contracts)
    solutions_out: List[Dict[str, Any]] = []
    for row in store.db.execute(
        "SELECT * FROM solutions WHERE workspace=? AND lifecycle IN ('verified','candidate') ORDER BY id",
        (ws,),
    ):
        spec = json.loads(row["body"])
        safe_spec, redactions = sanitize(spec, key)
        require(not redactions, f"Solution {row['id']} contains unredacted sensitive data")
        _assert_no_local_paths(safe_spec, f"Solution {row['id']}")

        uses_runner = any(st["op"] == "run_validator" for st in all_steps(safe_spec))
        history_rows = store.db.execute(
            "SELECT version, body FROM solution_versions WHERE workspace=? AND id=? ORDER BY version ASC",
            (ws, row["id"]),
        ).fetchall()
        lineage_chain = [_portable_fingerprint("solution", json.loads(h["body"])) for h in history_rows]
        cur_sha = _portable_fingerprint("solution", safe_spec)
        if not lineage_chain or lineage_chain[-1] != cur_sha:
            lineage_chain.append(cur_sha)
        parent_sha = lineage_chain[-2] if len(lineage_chain) >= 2 else None

        verified_cases = []
        for c_row in store.db.execute(
            "SELECT c.parameters_sha, r.body, r.updated_at FROM solution_cases c "
            "JOIN solution_runs r ON r.id=c.run_id "
            "WHERE c.workspace=? AND c.solution=? AND c.version=?",
            (ws, row["id"], row["version"]),
        ):
            run_body = json.loads(c_row["body"])
            verified_cases.append({
                "parameters": run_body.get("parameters", {}),
                "parameters_sha": c_row["parameters_sha"],
                "verified_at": c_row["updated_at"],
            })

        prof = tok_profiles.get(("solution", row["id"]), {})
        solutions_out.append({
            "schema": ITEM_SCHEMA,
            "kind": "solution",
            "id": row["id"],
            "workspace": ws,
            "platform": platform,
            "lifecycle": row["lifecycle"],
            "uses_external_runner": uses_runner,
            "lineage": {
                "content_sha256": cur_sha,
                "parent_sha256": parent_sha,
                "ancestors": lineage_chain[:-1][-16:],
                "origin_machine": origin,
                "updated_at": row["updated_at"],
            },
            "economics": {
                "acquisition_tokens": int(prof.get("acquisition_tokens", 3000)),
                "injected_tokens": int(prof.get("injected_tokens", 0)),
                "basis": str(prof.get("basis", "contract_steps")),
            },
            "human_layer": {
                "title": safe_spec["problem"]["goal"],
                "intent": safe_spec["problem"]["intent"],
                "patterns": safe_spec["problem"]["patterns"],
            },
            "spec": safe_spec,
            "verified_cases": verified_cases,
        })

    # 4) Sharded L1 Themes (pruned to exported or valid members)
    themes_out: List[Dict[str, Any]] = []
    for row in store.db.execute("SELECT id,body FROM themes WHERE workspace=? ORDER BY id", (ws,)):
        theme_spec = json.loads(row["body"])
        safe_theme, redactions = sanitize(theme_spec, key)
        require(not redactions, f"Theme {row['id']} contains sensitive data")
        _assert_no_local_paths(safe_theme, f"Theme {row['id']}")
        themes_out.append(safe_theme)

    total_acq_tokens = (
        sum(m["economics"]["acquisition_tokens"] for m in memos_out)
        + sum(c["economics"]["acquisition_tokens"] for c in claims_out)
        + sum(s["economics"]["acquisition_tokens"] for s in solutions_out)
    )

    files_payload: Dict[str, bytes] = {
        "doctrine/memos.json": canonical(memos_out).encode("utf-8"),
        "doctrine/claims.json": canonical(claims_out).encode("utf-8"),
        "doctrine/solutions.json": canonical(solutions_out).encode("utf-8"),
        "doctrine/themes.json": canonical(themes_out).encode("utf-8"),
    }
    for sha, blob_bytes in sorted(evidence_blobs.items()):
        files_payload[f"evidence/{sha}.blob"] = blob_bytes

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "format_version": 1,
        "workspace": ws,
        "platform": platform,
        "origin_machine": origin,
        "exported_at": exported_at,
        "include_runtime_claims": bool(include_runtime_claims),
        "counts": {
            "memos": len(memos_out),
            "claims": len(claims_out),
            "solutions": len(solutions_out),
            "themes": len(themes_out),
            "evidence_blobs": len(evidence_blobs),
            "acquisition_tokens": total_acq_tokens,
        },
        "files": {name: digest(raw) for name, raw in sorted(files_payload.items())},
    }
    files_payload["manifest.json"] = canonical(manifest).encode("utf-8")
    _write_deterministic_tar_gz(out_path, files_payload)

    return {
        "status": "BUNDLE_EXPORTED",
        "bundle_path": str(out_path),
        "size_bytes": out_path.stat().st_size,
        "bundle_sha256": file_digest(out_path),
        "manifest": manifest,
    }


def _load_and_verify_bundle_archive(bundle_path: Path) -> Tuple[Dict[str, Any], Dict[str, bytes]]:
    path = bundle_path.expanduser().resolve()
    require(path.is_file(), f"Bundle file does not exist: {path}")
    require(0 < path.stat().st_size <= MAX_BUNDLE_BYTES, "Bundle file empty or exceeds 64 MiB")
    try:
        with tarfile.open(path, mode="r:gz") as tf:
            members = _safe_tar_members(tf)
    except tarfile.TarError as exc:
        raise RuntimeErrorBase(f"Invalid .noomesh archive: {exc}") from exc

    require("manifest.json" in members, "Bundle is missing manifest.json")
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    object_fields(
        manifest,
        {"schema", "format_version", "workspace", "platform", "origin_machine",
         "exported_at", "counts", "files"},
        {"include_runtime_claims"},
    )
    require(manifest["schema"] == BUNDLE_SCHEMA and manifest["format_version"] == 1,
            "Unsupported .noomesh bundle schema or format version")
    require(manifest["platform"] in ("android", "ios", "generic"), "Invalid bundle platform")
    declared_files = manifest["files"]
    require(isinstance(declared_files, dict) and len(declared_files) >= 4, "Invalid manifest files map")
    actual_non_manifest = {k: v for k, v in members.items() if k != "manifest.json"}
    require(set(actual_non_manifest.keys()) == set(declared_files.keys()),
            "Bundle archive file inventory does not match manifest")
    for rel_name, expected_sha in declared_files.items():
        require(SHA256.fullmatch(str(expected_sha)) is not None, f"Invalid SHA-256 in manifest for {rel_name}")
        require(digest(actual_non_manifest[rel_name]) == expected_sha,
                f"Bundle integrity check failed for {rel_name}")
    return manifest, members


def inspect_bundle(bundle_path: Path) -> Dict[str, Any]:
    manifest, members = _load_and_verify_bundle_archive(bundle_path)
    memos = json.loads(members["doctrine/memos.json"].decode("utf-8"))
    claims = json.loads(members["doctrine/claims.json"].decode("utf-8"))
    solutions = json.loads(members["doctrine/solutions.json"].decode("utf-8"))
    return {
        "status": "BUNDLE_VALID",
        "bundle_path": str(bundle_path.expanduser().resolve()),
        "bundle_sha256": file_digest(bundle_path.expanduser().resolve()),
        "manifest": manifest,
        "titles": {
            "memos": [{"id": m["id"], "title": m["human_layer"]["title"]} for m in memos],
            "claims": [{"id": c["id"], "title": c["human_layer"]["title"]} for c in claims],
            "solutions": [{"id": s["id"], "title": s["human_layer"]["title"]} for s in solutions],
        },
    }


def _attest_anchors_on_checkout(ws_root: Path, anchors: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    if not anchors:
        return ("APPLICABLE", [])
    drift: List[str] = []
    for anchor in anchors:
        validate_anchor(anchor)
        try:
            ver = verify_anchor(ws_root, anchor, require_exact=False)
            if ver["status"] == "SOFT_DRIFT":
                drift.append(anchor["path"])
        except (RuntimeErrorBase, OSError, ValueError):
            return ("BRANCH_MISMATCH", [])
    return ("SOFT_DRIFT" if drift else "APPLICABLE", drift)


def _record_conflict(store: Store, workspace: str, kind: str, subject: str,
                     local_sha: str, remote_sha: str, origin_machine: str,
                     remote_item: Dict[str, Any]) -> str:
    cid = digest(canonical([workspace, kind, subject, local_sha, remote_sha]).encode())
    ts = now()
    store.db.execute(
        "INSERT INTO doctrine_conflicts VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET state='PENDING', updated_at=excluded.updated_at",
        (cid, workspace, kind, subject, local_sha, remote_sha,
         origin_machine[:120], canonical(remote_item), "PENDING", ts, ts),
    )
    return cid


def list_conflicts(store: Store, workspace: Optional[str] = None) -> Dict[str, Any]:
    if workspace:
        rows = store.db.execute(
            "SELECT * FROM doctrine_conflicts WHERE workspace=? AND state='PENDING' ORDER BY updated_at DESC",
            (workspace,),
        ).fetchall()
    else:
        rows = store.db.execute(
            "SELECT * FROM doctrine_conflicts WHERE state='PENDING' ORDER BY updated_at DESC"
        ).fetchall()
    items = []
    for r in rows:
        remote_obj = json.loads(r["remote_body"])
        items.append({
            "conflict_id": r["id"],
            "workspace": r["workspace"],
            "kind": r["kind"],
            "subject": r["subject"],
            "origin_machine": r["origin_machine"],
            "local_sha256": r["local_sha256"],
            "remote_sha256": r["remote_sha256"],
            "remote_title": remote_obj.get("human_layer", {}).get("title", r["subject"]),
            "updated_at": r["updated_at"],
        })
    return {"status": "OK", "pending_conflicts": len(items), "conflicts": items}


def import_bundle(store: Store, bundle_path: Path,
                  target_workspace: Optional[str] = None,
                  approve: bool = False) -> Dict[str, Any]:
    require(approve, "Explicit approval required to import a .noomesh doctrine bundle")
    manifest, members = _load_and_verify_bundle_archive(bundle_path)
    ws = target_workspace or manifest["workspace"]
    ws_row = store.workspace(ws)
    require(ws_row["platform"] == manifest["platform"],
            f"Platform mismatch: bundle is '{manifest['platform']}' but local workspace '{ws}' is '{ws_row['platform']}'")
    ws_root = Path(ws_row["root"]).resolve()
    require(ws_root.is_dir(), f"Local workspace checkout directory missing: {ws_root}")

    origin_machine = str(manifest["origin_machine"])[:120]
    key = privacy_key(store)
    memos_in = json.loads(members["doctrine/memos.json"].decode("utf-8"))
    claims_in = json.loads(members["doctrine/claims.json"].decode("utf-8"))
    solutions_in = json.loads(members["doctrine/solutions.json"].decode("utf-8"))
    themes_in = json.loads(members["doctrine/themes.json"].decode("utf-8"))

    counts = {
        "imported": 0,
        "fast_forwarded": 0,
        "noop": 0,
        "conflicts": 0,
        "applicable": 0,
        "soft_drift": 0,
        "branch_mismatch": 0,
        "runner_reapproval_needed": 0,
        "inherited_acquisition_tokens": 0,
    }
    item_reports: List[Dict[str, Any]] = []

    # 1) Import Memos with lineage check & local source re-attestation
    for item in memos_in:
        mid = str(item["id"])
        identifier(mid, "Memo ID")
        remote_spec = _rewrite_workspace_spec("memo", item["spec"], ws)
        safe_spec, redactions = sanitize(remote_spec, key)
        require(not redactions, f"Bundle memo {mid} failed local privacy check")
        _assert_no_local_paths(safe_spec, f"Bundle memo {mid}")
        validate_memo(store, safe_spec)

        remote_sha = _portable_fingerprint("memo", safe_spec)
        lineage = item.get("lineage", {})
        require(lineage.get("content_sha256") == remote_sha, f"Lineage SHA mismatch on memo {mid}")
        ancestors = set(lineage.get("ancestors") or [])
        if lineage.get("parent_sha256"):
            ancestors.add(lineage["parent_sha256"])

        attest_state, drift = _attest_anchors_on_checkout(
            ws_root, safe_spec["verification"]["anchors"]
        )
        body_str = canonical(safe_spec)
        fp = digest(body_str.encode())
        ts = now()

        action = "IMPORTED"
        with store.transaction():
            local_row = store.db.execute(
                "SELECT * FROM memos WHERE workspace=? AND id=?", (ws, mid)
            ).fetchone()
            if local_row is not None:
                local_spec = json.loads(local_row["body"])
                local_sha = _portable_fingerprint("memo", local_spec)
                if local_sha == remote_sha and local_row["lifecycle"] == "active":
                    counts["noop"] += 1
                    action = "NOOP"
                else:
                    # Check if remote is already an ancestor of local (local is strictly newer -> keep local)
                    local_hist = {
                        _portable_fingerprint("memo", json.loads(r["body"]))
                        for r in store.db.execute(
                            "SELECT body FROM memo_versions WHERE workspace=? AND id=?", (ws, mid)
                        )
                    }
                    if remote_sha in local_hist:
                        counts["noop"] += 1
                        action = "LOCAL_NEWER_KEPT"
                    elif local_sha in ancestors:
                        new_ver = int(local_row["version"]) + 1
                        store.db.execute(
                            "UPDATE memos SET version=?,lifecycle='active',body=?,fingerprint=?,updated_at=? "
                            "WHERE workspace=? AND id=?",
                            (new_ver, body_str, fp, ts, ws, mid),
                        )
                        store.db.execute(
                            "INSERT INTO memo_versions VALUES (?,?,?,?,?)",
                            (ws, mid, new_ver, body_str, fp),
                        )
                        record_usage(
                            store, ws, "memo", mid, new_ver, "verified_success",
                            "bundle-import", f"bundle:{origin_machine}:{remote_sha[:16]}",
                            occurred_at=ts,
                        )
                        counts["fast_forwarded"] += 1
                        action = "FAST_FORWARDED"
                    else:
                        _record_conflict(store, ws, "memo", mid, local_sha, remote_sha, origin_machine, item)
                        counts["conflicts"] += 1
                        action = "CONFLICT_PENDING"
            else:
                store.db.execute(
                    "INSERT INTO memos VALUES (?,?,?,?,?,?,?)",
                    (mid, ws, 1, "active", body_str, fp, ts),
                )
                store.db.execute(
                    "INSERT INTO memo_versions VALUES (?,?,?,?,?)",
                    (ws, mid, 1, body_str, fp),
                )
                record_usage(
                    store, ws, "memo", mid, 1, "verified_success",
                    "bundle-import", f"bundle:{origin_machine}:{remote_sha[:16]}",
                    occurred_at=ts,
                )
                counts["imported"] += 1

        if action in ("IMPORTED", "FAST_FORWARDED"):
            econ = item.get("economics", {})
            acq = int(econ.get("acquisition_tokens", 0) or 0)
            inj = int(econ.get("injected_tokens", 150) or 150)
            if acq > 0:
                record_token_baseline(
                    store, ws, "memo", mid, acq, injected_tokens=inj,
                    basis=str(econ.get("basis", "bundle_inherited"))[:32],
                    detail=f"Inherited from {origin_machine}",
                )
                counts["inherited_acquisition_tokens"] += acq

        if action != "CONFLICT_PENDING":
            if attest_state == "APPLICABLE":
                counts["applicable"] += 1
            elif attest_state == "SOFT_DRIFT":
                counts["soft_drift"] += 1
            else:
                counts["branch_mismatch"] += 1

        item_reports.append({
            "kind": "memo",
            "id": mid,
            "title": safe_spec["title"],
            "action": action,
            "local_status": attest_state,
            "anchor_drift": drift,
        })

    # 2) Import Evidence Blobs & Portable Claims
    with store.transaction():
        snapshot = store.snapshot()
        snapshot_changed = False
        for item in claims_in:
            cid = str(item["id"])
            identifier(cid, "Claim ID")
            remote_spec = _rewrite_workspace_spec("claim", item["spec"], ws)
            safe_spec, redactions = sanitize(remote_spec, key)
            require(not redactions, f"Bundle claim {cid} failed local privacy check")
            _assert_no_local_paths(safe_spec, f"Bundle claim {cid}")

            # Install and verify each referenced evidence blob, remapping evidence_id if workspace ID changed
            eid_remap: Dict[str, str] = {}
            for ev_info in item.get("evidence", []):
                old_eid = str(ev_info["evidence_id"])
                sha = str(ev_info["sha256"])
                blob_key = f"evidence/{sha}.blob"
                require(blob_key in members, f"Missing evidence blob {sha[:12]} for claim {cid}")
                raw_bytes = members[blob_key]
                require(0 < len(raw_bytes) <= MAX_EVIDENCE_BYTES and digest(raw_bytes) == sha,
                        f"Corrupted evidence blob {sha[:12]}")
                mtype = str(ev_info["media_type"])
                require(mtype in ("application/json", "application/xml", "image/png"),
                        "Unsupported evidence media type in bundle")
                if mtype == "application/json":
                    json.loads(raw_bytes)
                elif mtype == "application/xml":
                    require(b"<!DOCTYPE" not in raw_bytes.upper() and b"<!ENTITY" not in raw_bytes.upper(),
                            "XML entities prohibited")
                    ET.fromstring(raw_bytes)
                else:
                    require(raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"), "Invalid PNG evidence")

                ev_conds = ev_info["conditions"]
                validate_conditions(ev_conds)
                new_eid = digest(canonical({
                    "workspace": ws,
                    "platform": manifest["platform"],
                    "conditions": ev_conds,
                    "media_type": mtype,
                    "sha256": sha,
                }).encode())
                eid_remap[old_eid] = new_eid

                blob_file = store.blobs / sha
                if not blob_file.exists():
                    blob_file.write_bytes(raw_bytes)
                else:
                    require(file_digest(blob_file) == sha, "Local evidence blob hash mismatch")
                ev_event_id = f"bundle-{new_eid[:24]}"
                store.db.execute(
                    "INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                    (new_eid, ev_event_id, ws, manifest["platform"],
                     canonical(ev_conds), mtype, sha, now()),
                )

            for chk in safe_spec["checks"]:
                chk["evidence_id"] = eid_remap.get(chk["evidence_id"], chk["evidence_id"])
                _, raw_ev = store.evidence(chk["evidence_id"])
                ev_row = store.db.execute(
                    "SELECT media_type FROM evidence WHERE id=?", (chk["evidence_id"],)
                ).fetchone()
                require(check_evidence(chk, raw_ev, ev_row["media_type"]),
                        f"Bundle claim {cid} failed local predicate verification against its own evidence")

            # Ensure provenance timestamp is refreshed to import time if missing so source freshness holds
            prov = dict(safe_spec.get("provenance") or {})
            prov.setdefault("kind", "source_audit")
            prov["validated_at"] = now()
            safe_spec["provenance"] = prov
            store.validate_spec(safe_spec)

            remote_sha = _portable_fingerprint("claim", safe_spec)
            attest_state, drift = _attest_anchors_on_checkout(ws_root, safe_spec.get("anchors", []))

            existing_claim = snapshot.get(cid)
            action = "IMPORTED"
            if existing_claim is not None:
                local_spec_cmp = json.loads(canonical(existing_claim["spec"]))
                local_spec_cmp.get("provenance", {}).pop("validated_at", None)
                remote_spec_cmp = json.loads(canonical(safe_spec))
                remote_spec_cmp.get("provenance", {}).pop("validated_at", None)
                local_sha = _portable_fingerprint("claim", local_spec_cmp)
                cmp_remote_sha = _portable_fingerprint("claim", remote_spec_cmp)
                if local_sha == cmp_remote_sha and existing_claim.get("lifecycle") == "active":
                    counts["noop"] += 1
                    action = "NOOP"
                else:
                    _record_conflict(store, ws, "claim", cid, local_sha, cmp_remote_sha, origin_machine, item)
                    counts["conflicts"] += 1
                    action = "CONFLICT_PENDING"
            if action == "IMPORTED":
                ver_num = (int(existing_claim["version"]) + 1) if existing_claim else 1
                claim_record = {
                    "spec": safe_spec,
                    "version": ver_num,
                    "lifecycle": "active",
                    "dependency_versions": item.get("dependency_versions", {}),
                    "verification": {
                        "verdict": "PASS",
                        "checks": [
                            {"kind": c["kind"], "evidence_id": c["evidence_id"], "passed": True}
                            for c in safe_spec["checks"]
                        ],
                        "anchor_drift": drift,
                        "meaning": "Declared predicates passed; re-attested on receiving Mac checkout",
                    },
                }
                snapshot[cid] = claim_record
                store.db.execute(
                    "INSERT INTO claims VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "version=excluded.version,body=excluded.body,updated_at=excluded.updated_at",
                    (cid, ver_num, canonical(claim_record), now()),
                )
                record_usage(
                    store, ws, "claim", cid, ver_num, "verified_success",
                    "bundle-import", f"bundle:{origin_machine}:{remote_sha[:16]}",
                )
                snapshot_changed = True
                counts["imported"] += 1

                econ = item.get("economics", {})
                acq = int(econ.get("acquisition_tokens", 0) or 0)
                inj = int(econ.get("injected_tokens", 140) or 140)
                if acq > 0:
                    store.db.execute(
                        "INSERT INTO token_baselines VALUES (?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(workspace,kind,subject) DO UPDATE SET "
                        "acquisition_tokens=excluded.acquisition_tokens,"
                        "reusable_saved_tokens=excluded.reusable_saved_tokens,"
                        "injected_tokens=excluded.injected_tokens,"
                        "basis=excluded.basis,detail=excluded.detail,updated_at=excluded.updated_at",
                        (ws, "claim", cid, acq, max(0, acq - inj), inj,
                         str(econ.get("basis", "bundle_inherited"))[:32],
                         f"Inherited from {origin_machine}", now()),
                    )
                    counts["inherited_acquisition_tokens"] += acq

            if action != "CONFLICT_PENDING":
                if attest_state == "APPLICABLE":
                    counts["applicable"] += 1
                elif attest_state == "SOFT_DRIFT":
                    counts["soft_drift"] += 1
                else:
                    counts["branch_mismatch"] += 1

            item_reports.append({
                "kind": "claim",
                "id": cid,
                "title": prov.get("title", cid),
                "action": action,
                "local_status": attest_state,
                "anchor_drift": drift,
            })

        if snapshot_changed:
            snap_body = canonical(snapshot)
            new_rev = digest(snap_body.encode())
            store.db.execute(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?)",
                (new_rev, snap_body, now()),
            )
            store.db.execute(
                "INSERT INTO state VALUES ('revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (new_rev,),
            )

    # 3) Import P/R/Q Solutions (pure file contracts activate directly if preconditions pass;
    #    contracts invoking external runners downgrade safely to 'candidate' until locally re-verified!)
    for item in solutions_in:
        sid = str(item["id"])
        identifier(sid, "Solution ID")
        remote_spec = _rewrite_workspace_spec("solution", item["spec"], ws)
        safe_spec, redactions = sanitize(remote_spec, key)
        require(not redactions, f"Bundle solution {sid} failed local privacy check")
        _assert_no_local_paths(safe_spec, f"Bundle solution {sid}")

        uses_runner = any(st["op"] == "run_validator" for st in all_steps(safe_spec))
        if not uses_runner:
            validate_solution_spec(store, safe_spec)

        remote_sha = _portable_fingerprint("solution", safe_spec)
        lineage = item.get("lineage", {})
        ancestors = set(lineage.get("ancestors") or [])
        if lineage.get("parent_sha256"):
            ancestors.add(lineage["parent_sha256"])

        target_lifecycle = "candidate" if uses_runner else str(item.get("lifecycle", "candidate"))
        body_str = canonical(safe_spec)
        fp = digest(body_str.encode())
        ts = now()

        action = "IMPORTED"
        installed_ver = 1
        with store.transaction():
            local_row = store.db.execute(
                "SELECT * FROM solutions WHERE workspace=? AND id=?", (ws, sid)
            ).fetchone()
            if local_row is not None:
                local_sha = _portable_fingerprint("solution", json.loads(local_row["body"]))
                if local_sha == remote_sha:
                    counts["noop"] += 1
                    action = "NOOP"
                    installed_ver = int(local_row["version"])
                elif local_sha in ancestors:
                    installed_ver = int(local_row["version"]) + 1
                    store.db.execute(
                        "UPDATE solutions SET version=?,lifecycle=?,body=?,fingerprint=?,updated_at=? "
                        "WHERE workspace=? AND id=?",
                        (installed_ver, target_lifecycle, body_str, fp, ts, ws, sid),
                    )
                    store.db.execute(
                        "INSERT INTO solution_versions VALUES (?,?,?,?,?)",
                        (ws, sid, installed_ver, body_str, fp),
                    )
                    counts["fast_forwarded"] += 1
                    action = "FAST_FORWARDED"
                else:
                    _record_conflict(store, ws, "solution", sid, local_sha, remote_sha, origin_machine, item)
                    counts["conflicts"] += 1
                    action = "CONFLICT_PENDING"
            else:
                installed_ver = 1
                store.db.execute(
                    "INSERT INTO solutions VALUES (?,?,?,?,?,?,?)",
                    (sid, ws, 1, target_lifecycle, body_str, fp, ts),
                )
                store.db.execute(
                    "INSERT INTO solution_versions VALUES (?,?,?,?,?)",
                    (ws, sid, 1, body_str, fp),
                )
                counts["imported"] += 1

        # Re-verify pure file-inspection parameter cases directly against the receiving Mac's checkout!
        # This generates a genuine cryptographic proof blob on Mac B without faking proof on mismatched branches.
        if action in ("IMPORTED", "FAST_FORWARDED") and not uses_runner and item.get("lifecycle") == "verified":
            from .solutions import execute_solution
            verified_any = False
            for idx, vc in enumerate(item.get("verified_cases", [])):
                params = vc.get("parameters", {})
                with store.transaction():
                    store.db.execute(
                        "UPDATE solutions SET lifecycle='candidate' WHERE workspace=? AND id=?",
                        (ws, sid),
                    )
                trial_res = execute_solution(
                    store, ws, sid, "bundle-import", f"reattest-{idx}",
                    params, {}, verify_candidate=True, approve=True,
                )
                if trial_res.get("status") == "LOCAL_SOLVED":
                    verified_any = True
            with store.transaction():
                store.db.execute(
                    "UPDATE solutions SET lifecycle=? WHERE workspace=? AND id=?",
                    ("verified" if verified_any else "candidate", ws, sid),
                )

        if action in ("IMPORTED", "FAST_FORWARDED"):
            econ = item.get("economics", {})
            acq = int(econ.get("acquisition_tokens", 0) or 0)
            inj = int(econ.get("injected_tokens", 0) or 0)
            if acq > 0:
                record_token_baseline(
                    store, ws, "solution", sid, acq, injected_tokens=inj,
                    basis=str(econ.get("basis", "bundle_inherited"))[:32],
                    detail=f"Inherited from {origin_machine}",
                )
                counts["inherited_acquisition_tokens"] += acq

        if uses_runner:
            local_sol_status = "RUNNER_REAPPROVAL_REQUIRED"
            if action != "CONFLICT_PENDING":
                counts["runner_reapproval_needed"] += 1
        else:
            sol_record = {"id": sid, "workspace": ws, "version": installed_ver, "spec": safe_spec}
            sample_params = (
                item["verified_cases"][0].get("parameters", {})
                if item.get("verified_cases") else {}
            )
            pre_checks = check_preconditions(store, sol_record, sample_params, {})
            if any(c["verdict"] == "FAIL" for c in pre_checks):
                local_sol_status = "BRANCH_MISMATCH"
                if action != "CONFLICT_PENDING":
                    counts["branch_mismatch"] += 1
            else:
                local_sol_status = "APPLICABLE"
                if action != "CONFLICT_PENDING":
                    counts["applicable"] += 1

        item_reports.append({
            "kind": "solution",
            "id": sid,
            "title": safe_spec["problem"]["goal"],
            "action": action,
            "local_status": local_sol_status,
        })

    # 4) Merge L1 Themes non-destructively (union members so local shard additions are preserved)
    themes_merged = 0
    with store.transaction():
        for remote_theme in themes_in:
            tid = str(remote_theme["id"])
            identifier(tid, "Theme")
            safe_theme, redactions = sanitize(remote_theme, key)
            require(not redactions, f"Bundle theme {tid} failed privacy check")
            local_t = store.db.execute(
                "SELECT body FROM themes WHERE workspace=? AND id=?", (ws, tid)
            ).fetchone()
            if local_t is None:
                store.db.execute(
                    "INSERT INTO themes VALUES (?,?,?)",
                    (ws, tid, canonical(safe_theme)),
                )
                themes_merged += 1
            else:
                cur_t = json.loads(local_t["body"])
                merged_members = list(dict.fromkeys(
                    list(cur_t.get("members", [])) + list(safe_theme.get("members", []))
                ))[:32]
                merged_aliases = list(dict.fromkeys(
                    list(cur_t.get("aliases", [])) + list(safe_theme.get("aliases", []))
                ))[:32]
                if merged_members != cur_t.get("members") or merged_aliases != cur_t.get("aliases"):
                    cur_t["members"] = merged_members
                    cur_t["aliases"] = merged_aliases
                    cur_t["version"] = int(cur_t.get("version", 1)) + 1
                    store.db.execute(
                        "UPDATE themes SET body=? WHERE workspace=? AND id=?",
                        (canonical(cur_t), ws, tid),
                    )
                    themes_merged += 1
        # Invalidate cached derived context views so next query reflects imported doctrine immediately
        store.db.execute("DELETE FROM context_views")

    return {
        "status": "BUNDLE_IMPORTED",
        "workspace": ws,
        "origin_machine": origin_machine,
        "counts": counts,
        "themes_merged": themes_merged,
        "items": item_reports,
    }
