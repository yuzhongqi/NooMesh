---
description: "Use when a new local VS Code session needs project knowledge, verified app observations, or legacy background for a registered workspace."
---

# Shared local runtime knowledge

Template: install as a personal instruction in the active VS Code profile.
Replace `/ABSOLUTE/PATH/NooMesh/tools/llr.py` with the checkout
path. Do not install this template unchanged. Keep private state outside Git.

For unrelated repositories or general questions, no memory action is required.

When a local private coding task targets a registered workspace (including after context recovery):

1. Check the `llr.session_index.v1` L0 header injected at `sessionStart` when present. Before implementation planning, call the `local_learning_search` or `local_experience_context` MCP tool with the actual `cwd`, actual business task, and a unique session ID (fallback CLI: `python3 /ABSOLUTE/PATH/NooMesh/tools/llr.py search --cwd "<actual checkout directory>" --query "<actual business task>" --session "<unique session ID>"`).
2. `DISCOVERED` locates the workspace; check `reuse` (`APPLICABLE`, `CONDITIONS_UNKNOWN`, `PRECONDITIONS_REQUIRED`, `BLOCKED`) and current source anchors before relying on a claim or solution. Report `UNKNOWN_WORKSPACE` or `AMBIGUOUS` rather than guessing another project's knowledge.
3. Runtime is the authority for published observations and verified solutions. Only fall back to legacy `Userbot` recall when runtime search returns `NO_MATCH` and historical architectural notes are needed; never run both startup procedures upfront by default, and never treat `UNVERIFIED_LEGACY` as verified fact.
4. When asked for runtime verification, discover `local_learning_validators` and invoke `local_learning_validate` within its preapproved scope (it persists and publishes passing predicates automatically). Never register or widen a validator or upload private knowledge without explicit user approval.
