---
name: qa-issues-system-prompt-store
description: QA findings for Issue #9 system_prompt.py — sort order, line endings, tests, integration
metadata:
  type: feedback
  date: 2026-01-26
  issue: #9
---

# QA Report: `system_prompt.py` (Issue #9)

## 1. Import: PASS

`from gaia.system_prompt import SystemPromptStore` resolves without error.

## 2. Existing Tests: PASS

All 261 existing tests pass. No regressions.

## 3. Spec Compliance (against FIX_PROPOSAL.md Issue #9)

| Requirement | Status |
|---|---|
| Numbered backups v1-v10, rotate down | PASS |
| Manifest with timestamp + length | PASS |
| Empty-prompt guard raises ValueError | PASS |
| Rollback method | PASS |
| list_backups method | PASS |
| Location: `~/.gaia/prompts/` | PASS |

Core functionality matches the spec.

## 4. Issues Found

### Issue A (Bug): `list_backups` sort order contradicts docstring

**File:** `src/gaia/system_prompt.py`, line ~178

**Problem:** The docstring says "Return manifest entries sorted newest first." But the sort is `entries.sort(key=lambda e: e["version"], reverse=True)`. Since v1 is the newest backup (most recently saved) and v10 is the oldest, `reverse=True` sorts oldest-first. The first entry returned is v10, not v1.

**Fix:** Remove `reverse=True` from the sort on line 178:

```python
# Before
entries.sort(key=lambda e: e["version"], reverse=True)

# After
entries.sort(key=lambda e: e["version"])
```

---

### Issue B (Bug): Line-ending translation during save/load

**File:** `src/gaia/system_prompt.py`, lines ~66, ~91

**Problem:** `Path.write_text()` and `Path.read_text()` use universal newlines mode by default. On macOS, `\r\n` in a saved prompt is translated to `\n` during write, and `read_text` does not reverse this. Content roundtrip is not lossless for CRLF line endings.

**Fix:** Pass `newline=""` to disable newline translation:

```python
# Line ~66 — save_prompt
tmp.write_text(text, encoding="utf-8", newline="")

# Line ~91 — load_prompt
return self._prompt_file.read_text(encoding="utf-8", newline="")

# Line ~126 — rollback
self._prompt_file.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8", newline="")
```

---

### Issue C (Missing tests): No unit tests for `SystemPromptStore`

**Problem:** The project spec states: *"If no tests exist for the changed code, reject and send back to the coder with instructions to add unit tests."* No test file imports or references `SystemPromptStore`.

**Fix:** Create `tests/test_system_prompt_store.py` covering:

| Test | What it verifies |
|---|---|
| `test_save_load_roundtrip` | Save text, load it back, matches exactly |
| `test_empty_prompt_raises_valueerror` | `save_prompt("   ")` raises `ValueError` |
| `test_backup_rotation` | Save twice → v1 and v2 backups exist with correct content |
| `test_max_backups_limit` | Save 12 times → only v1–v10 exist (v11+ pruned) |
| `test_rollback_success` | Rollback to v2 restores old content |
| `test_rollback_not_found` | Rollback to non-existent version raises `ValueError` |
| `test_list_backups_order` | Entries sorted newest-first (v1 before v10) |
| `test_manifest_integrity` | Manifest file contains valid JSON with expected fields |
| `test_atomic_write` | `.tmp` files cleaned up after save (no orphan temp files) |

---

### Issue D (Dead code): Module is not wired into the app

**Problem:** `SystemPromptStore` is not imported by any other module. No slash command, no settings modal, no app logic references it. It is defined but never used.

**Fix:** Wire it into the app:

1. **In `gaia_tui_app.py` `__init__`:** Create `self._prompt_store = SystemPromptStore()`. On startup, load the stored prompt and use it as the default (or merge over `settings.json` as the current fallback).

2. **In `slash_commands.py` `/settings` case:** Replace the old CWD-relative `_AGENT_MD` backup logic with `self._app._prompt_store.save_prompt(settings.system_prompt)`.

3. **Optional:** Add `/prompt-list` and `/prompt-rollback` slash commands:
   - `/prompt-list` → `for entry in store.list_backups(): show entry`
   - `/prompt-rollback <n>` → `store.rollback(n)` and reconfigure the agent

---

## 5. Decision: REJECT

Per the spec, the module must have unit tests before approval. Issues to fix:

1. Add `tests/test_system_prompt_store.py` with coverage for save/load, backup rotation, empty-prompt guard, rollback, list_backups ordering, manifest integrity, and atomic writes.
2. Fix `list_backups` sort order (remove `reverse=True`, line ~178).
3. Fix line-ending translation (add `newline=""` to `write_text` and `read_text` calls).
4. Wire `SystemPromptStore` into the app (settings modal or slash commands).

After the coder addresses these, re-submit through the reviewer → QA pipeline.
