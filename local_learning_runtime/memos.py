"""Closed-loop Rule & Trap Memos grounded in verified workspace source/symbol anchors."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .core import (MAX_PACKET_BYTES, RuntimeErrorBase, Store, canonical, digest,
                   identifier, now, object_fields, require, validate_anchor, verify_anchor)
from .learning import privacy_key
from .privacy import sanitize

MEMO_KINDS = ("rule", "trap")
VERIFICATION_METHODS = ("source_symbol_contract", "test_case", "incident_fix")


def validate_memo(store: Store, spec: Dict[str, Any]) -> None:
    object_fields(spec, {"schema", "id", "workspace", "kind", "title",
                         "when", "rule", "trap", "verification"}, {"tags", "acquisition_tokens"})
    require(spec["schema"] == "llr.memo.v1", "Unknown memo schema")
    identifier(spec["id"], "Memo ID")
    store.workspace(spec["workspace"])
    require(spec["kind"] in MEMO_KINDS, "Memo kind must be rule or trap")
    for field, limit in (("title", 240), ("when", 500), ("rule", 2000), ("trap", 1000)):
        value = spec[field]
        require(isinstance(value, str) and 0 < len(value.strip()) <= limit,
                f"Memo {field} must be 1-{limit} non-whitespace characters")
    title_clean = spec["title"].strip()
    require(not title_clean.lower().startswith(("http://", "https://"))
            and "recorded runtime predicates" not in title_clean.lower()
            and not title_clean.startswith(("[{" , "{\"")),
            "Memo title must be a human-readable summary (<phenomenon/scenario>: <cause/rule>), not a URL or raw predicate dump")
    if "tags" in spec:
        tags = spec["tags"]
        require(isinstance(tags, list) and len(tags) <= 16
                and all(isinstance(t, str) and 0 < len(t.strip()) <= 64 for t in tags),
                "Memo tags must be up to 16 bounded strings")
    if "acquisition_tokens" in spec:
        acq = spec["acquisition_tokens"]
        require(type(acq) is int and 1 <= acq <= 10_000_000,
                "Memo acquisition_tokens must be an integer between 1 and 10000000")
    ver = spec["verification"]
    object_fields(ver, {"method", "summary", "anchors"})
    require(ver["method"] in VERIFICATION_METHODS, "Unknown memo verification method")
    require(isinstance(ver["summary"], str) and 0 < len(ver["summary"].strip()) <= 1000,
            "Memo verification summary must be 1-1000 characters")
    anchors = ver["anchors"]
    require(isinstance(anchors, list) and 1 <= len(anchors) <= 16,
            "Memo verification requires 1-16 source anchors")
    for anchor in anchors:
        validate_anchor(anchor)
    require(len(canonical(spec).encode()) <= MAX_PACKET_BYTES,
            "Memo exceeds 12000 UTF-8 bytes")


def verify_memo_spec(store: Store, spec: Dict[str, Any],
                     require_exact: bool = False) -> Dict[str, Any]:
    validate_memo(store, spec)
    scope = store.workspace(spec["workspace"])
    root = Path(scope["root"])
    results = [verify_anchor(root, anchor, require_exact=require_exact)
               for anchor in spec["verification"]["anchors"]]
    drift = [item["path"] for item in results if item["status"] == "SOFT_DRIFT"]
    return {"verdict": "PASS", "anchors": results, "anchor_drift": drift}


def get_memo(store: Store, workspace: str, memo_id: str) -> Dict[str, Any]:
    row = store.db.execute("SELECT * FROM memos WHERE workspace=? AND id=?",
                           (workspace, memo_id)).fetchone()
    require(row is not None, "Published memo not found")
    body = json.loads(row["body"])
    require(digest(row["body"].encode()) == row["fingerprint"], "Memo fingerprint mismatch")
    return {"id": row["id"], "workspace": row["workspace"], "version": row["version"],
            "lifecycle": row["lifecycle"], "fingerprint": row["fingerprint"],
            "updated_at": row["updated_at"], "spec": body}


def verify_memo_live(store: Store, workspace: str, memo_id: str) -> Dict[str, Any]:
    from .lifecycle import require_fresh
    record = get_memo(store, workspace, memo_id)
    require(record["lifecycle"] == "active", "Memo lifecycle is " + record["lifecycle"])
    require_fresh(store, workspace, "memo", memo_id)
    return verify_memo_spec(store, record["spec"], require_exact=False)


def propose_memo(store: Store, spec: Dict[str, Any], expected_version: int,
                 approve: bool, session: str = "author",
                 acquisition_tokens: Optional[int] = None) -> Dict[str, Any]:
    require(approve, "Explicit approval required to publish a rule or trap memo")
    require(type(expected_version) is int and expected_version >= 0, "Invalid expected version")
    identifier(session, "Session")
    if acquisition_tokens is not None:
        spec = dict(spec, acquisition_tokens=acquisition_tokens)
    validate_memo(store, spec)
    safe, redactions = sanitize(spec, privacy_key(store))
    require(not redactions, "Memo contains sensitive literals; sanitize before publishing")
    verification = verify_memo_spec(store, safe, require_exact=True)
    body = canonical(safe)
    fingerprint = digest(body.encode())
    timestamp = now()
    with store.transaction():
        old = store.db.execute("SELECT version,lifecycle,fingerprint FROM memos WHERE workspace=? AND id=?",
                               (safe["workspace"], safe["id"])).fetchone()
        require((old["version"] if old else 0) == expected_version, "Memo version conflict")
        if old and old["lifecycle"] == "active" and old["fingerprint"] == fingerprint:
            return {"status": "NOOP", "workspace": safe["workspace"], "id": safe["id"],
                    "version": expected_version, "fingerprint": fingerprint}
        version = expected_version + 1
        store.db.execute("INSERT INTO memos VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET "
                         "version=excluded.version,lifecycle=excluded.lifecycle,body=excluded.body,"
                         "fingerprint=excluded.fingerprint,updated_at=excluded.updated_at",
                         (safe["id"], safe["workspace"], version, "active", body, fingerprint, timestamp))
        store.db.execute("INSERT INTO memo_versions VALUES (?,?,?,?,?)",
                         (safe["workspace"], safe["id"], version, body, fingerprint))
        from .lifecycle import record as record_usage
        record_usage(store, safe["workspace"], "memo", safe["id"], version,
                     "verified_success", session, "memo-publish:" + fingerprint[:24],
                     occurred_at=timestamp)
    from .tokens import estimate_text_tokens, probe_copilot_turn_tokens, record_token_baseline
    inj_est = max(60, estimate_text_tokens(
        f"{safe['title']} {safe['when']} {safe['rule']} {safe['trap']}"
    ))
    explicit_acq = safe.get("acquisition_tokens")
    if isinstance(explicit_acq, int) and explicit_acq > 0:
        record_token_baseline(store, safe["workspace"], "memo", safe["id"],
                              explicit_acq, injected_tokens=inj_est, basis="measured")
    else:
        turn_tok = probe_copilot_turn_tokens(session)
        if turn_tok is not None:
            record_token_baseline(store, safe["workspace"], "memo", safe["id"],
                                  turn_tok, injected_tokens=inj_est, basis="turn_telemetry")
    return {"status": "PUBLISHED", "workspace": safe["workspace"], "id": safe["id"],
            "version": version, "fingerprint": fingerprint, "verification": verification}


def invalidate_memo(store: Store, workspace: str, memo_id: str, expected_version: int,
                    state: str, reason: str, approve: bool) -> Dict[str, Any]:
    require(approve, "Explicit approval required to invalidate a memo")
    require(state in ("stale", "conflicted", "superseded"), "Invalid memo invalidation state")
    require(isinstance(reason, str) and 0 < len(reason.strip()) <= 1000, "Provide a bounded reason")
    with store.transaction():
        row = store.db.execute("SELECT * FROM memos WHERE workspace=? AND id=?",
                               (workspace, memo_id)).fetchone()
        require(row is not None, "Memo not found")
        require(row["version"] == expected_version, "Memo version conflict")
        version = expected_version + 1
        store.db.execute("UPDATE memos SET version=?,lifecycle=?,updated_at=? WHERE workspace=? AND id=?",
                         (version, state, now(), workspace, memo_id))
        store.db.execute("INSERT INTO memo_versions VALUES (?,?,?,?,?)",
                         (workspace, memo_id, version, row["body"], row["fingerprint"]))
    return {"status": "INVALIDATED", "workspace": workspace, "id": memo_id,
            "version": version, "lifecycle": state}
