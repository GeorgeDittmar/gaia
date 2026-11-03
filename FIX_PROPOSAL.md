# G.A.I.A. — Fix Proposal (Highest-Priority Issues)

*Companion to [`issues.md`](issues.md). This document proposes concrete fixes for the highest-severity issues so they can be picked up and implemented by coding agents. Each entry gives the root cause, the exact changes (with code), files to touch, how to verify, and any blocking decision. Lower-severity items are summarized in the table at the end.*

> **Working model:** implement one issue at a time, run the test suite, submit through the project's reviewer → QA pipeline before moving on. Every fix lists the tests to add/change. Do **not** start a fix that depends on a blocking decision (marked ⚠) until that decision is made.

---

## Suggested implementation order

Grouped so dependencies fall naturally and each group is independently shippable:

1. **Config & I/O hardening** — fixes #2 and #12 (cheap, no behavior change, de-risks everything).
2. **Extraction consolidation** — fixes #5 and #8 (removes the biggest latency/cost problem and a race).
3. **Concurrency** — fix #3 (serialize LLM calls).
4. **Retention & scale** — fixes #6 and #7.
5. **Context management** — fix #4 (largest, do last).
6. **Security hardening** — fixes #9 and #1.

---

## Issue #2 — CWD-relative settings & log paths
**Severity: HIGH** · *issues.md §2.1*

**Root cause.** `SETTINGS_FILE = Path("settings.json")` and `log_dir = Path("logs")` are relative to the current working directory, so a console-script user gets a different settings file (and log dir) depending on where they launch `gaia` from — settings silently "disappear" between launches.

**Proposed fix.** Pin both under `~/.gaia/`, keep `SETTINGS_FILE` as a reassignable module global (the tests monkeypatch it).

In [`config.py`](src/gaia/config.py):
```python
from pathlib import Path
from os.path import expanduser

SETTINGS_DIR = Path(expanduser("~/.gaia"))
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
```

In `load_config`, before creating the file, ensure the parent exists:
```python
if not SETTINGS_FILE.exists():
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        save_config(Settings())
    except Exception:
        pass
    return Settings()
```

In [`gaia_tui_app.py` `_setup_logging`](src/gaia/gaia_tui_app.py:39-64):
```python
from gaia.config import SETTINGS_DIR
...
log_dir = SETTINGS_DIR / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"gaia-{ts}.log"
```

**Files:** `src/gaia/config.py`, `src/gaia/gaia_tui_app.py`
**Verify:** Launch `gaia` from an unrelated CWD (e.g. `cd /tmp && python -m gaia.cli`); confirm `~/.gaia/settings.json` is created and logs land in `~/.gaia/logs/`. Add a test asserting `SETTINGS_FILE.parent == SETTINGS_DIR`.
**Risk:** The `settings_path` fixture in `tests/conftest.py` reassigns `config_mod.SETTINGS_FILE`; keeping it a module global means that still works.

---

## Issue #12 — `save_config` is not atomic
**Severity: MEDIUM** (bundled with #2) · *issues.md §2.2*

**Root cause.** `save_config` overwrites `settings.json` in place; a crash mid-write corrupts it, and `load_config` silently falls back to defaults.

**Proposed fix.** Write to a temp file then `os.replace` (atomic on POSIX). Reuse the pattern already in the memory store.
```python
import os
def save_config(settings: Settings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, indent=4)
    os.replace(tmp, SETTINGS_FILE)
```
**Files:** `src/gaia/config.py`
**Verify:** `uv run pytest tests/test_config.py tests/test_settings_dataclass.py`.

---

## Issue #5 — Redundant 2–3× per-turn fact extraction
**Severity: HIGH** · *issues.md §4.2*

**Root cause.** Extraction fires in three overlapping places: inline after every turn in `acteract`, periodically every 3 turns (`run_agent_execution`), and "at shutdown" (`force_exit`, which races — see #8). Each call also hard-sleeps 2 s, roughly doubling local-LLM latency.

**Proposed fix — pick ONE strategy. Recommended: batch-only.**

1. **Remove the inline extraction** from `acteract`. Delete the block at [`base.py:597-604`](src/gaia/core/agent/base.py:597-604) (the `if self._memory ... post_turn_extract` block).
2. **Keep** the periodic extraction in `run_agent_execution` (`_EXTRACT_EVERY_N_TURNS`, currently 3).
3. **Remove** the shutdown extraction — delete `self._schedule_shutdown_extraction()` call in [`force_exit`](src/gaia/gaia_tui_app.py:435) ([`issues.md §3.1`](issues.md#31-final-shutdown-extraction-is-likely-a-race)). The last turn is already captured by the periodic pass.
4. **Fix the stale comment** in [`streaming.py:109-111`](src/gaia/streaming.py:109-111) that claims "batch-extracted once per session at shutdown."
5. Consider making the per-turn `asyncio.sleep(2)` conditional or removing it now that only one extraction path exists.

**Alternative:** per-turn inline (immediate availability for next-turn retrieval) — then delete the periodic `call_later` and the shutdown task. Document the trade-off in the PR.
**Files:** `src/gaia/core/agent/base.py`, `src/gaia/gaia_tui_app.py`, `src/gaia/streaming.py`
**Verify:** Confirm only one `post_turn_extract` call site remains. Add a test counting extraction LLM calls per N turns against a fake model (see `tests/conftest.py` `_make_mock_run_result`).
**Risk:** Extraction dedup (`semantic_similarity_search`) already prevents duplicates, so dropping a path is safe.

---

## Issue #8 — Final shutdown extraction is a race
**Severity: HIGH** · *issues.md §3.1*

**Root cause.** [`force_exit`](src/gaia/gaia_tui_app.py:425-444) calls `_schedule_shutdown_extraction` (which starts a `@work` task), then immediately calls `self.workers.cancel_all()` and `self.exit()`. The task is cancelled before it runs — the "final extraction" is a no-op.

**Proposed fix.** Remove the shutdown extraction entirely (see #5 step 3). If a final pass is desired, run it **synchronously and awaited** *before* cancelling workers:
```python
def force_exit(self) -> None:
    self.is_shutting_down = True
    if self._memory is not None and self._turn_count > 0:
        try:
            asyncio.run(self._GaiaTUIApp__core_agent.extract_from_history(max_turns=3))
        except Exception:
            logger.exception("shutdown extraction failed")
    if self._memory is not None:
        ... await _close_memory ...
    self.workers.cancel_all()
    self.exit()
```
**Files:** `src/gaia/gaia_tui_app.py`
**Verify:** Exit the app mid-session and confirm no "cancelled" traceback in the log.

---

## Issue #3 — Overlapping concurrent LLM requests
**Severity: HIGH** · *issues.md §3.3*

**Root cause.** Local backends run a single model slot. The main response, the planner, post-turn extraction, and summary regeneration all issue LLM calls concurrently, so requests queue/time out and the errors are silently swallowed.

**Proposed fix.** Serialize every LLM call behind a single asyncio semaphore in the `Gaia` agent.

Add to [`Gaia.__init__`](src/gaia/core/agent/base.py:143):
```python
self._llm_semaphore = asyncio.Semaphore(1)
```

Wrap each LLM call site in `async with self._llm_semaphore:`:
- Main response — [`acteract`](src/gaia/core/agent/base.py:556)
```python
async with self._llm_semaphore:
    async with self.__core_agent.run_stream(...) as result:
        async for chunk in result.stream_text(delta=True):
            ...
```
- Planner — [`acteract`](src/gaia/core/agent/base.py:513)
- Extraction — [`_extract_facts`](src/gaia/core/agent/base.py:709)
- Summary — [`generate_user_summary`](src/gaia/core/agent/base.py:464)

**Files:** `src/gaia/core/agent/base.py`
**Verify:** Drive two concurrent `acteract` calls against the mock in `tests/conftest.py` and assert the extraction/summary tasks block until the streaming completes (e.g. by having the mock record call start/end order).
**Risk:** Holding the semaphore while streaming means an abandoned generator holds it until closed — acceptable for this app.

---

## Issue #6 — Episodic memory is never pruned
**Severity: HIGH** · *issues.md §6.1*

**Root cause.** `episodic_prune` is defined in both stores and the protocol and tested, but **no app code calls it** (verified). Memory grows unbounded.

**Proposed fix.** Add a retention setting and a scheduled prune.

1. Add `retention_days: int = 30` to `MemoryConfig` (frozen dataclass) and thread it through `from_dict`/`to_dict` in [`config.py`](src/gaia/config.py).
2. Add a retention `@work` task in [`gaia_tui_app.py`](src/gaia/gaia_tui_app.py) that calls `self._memory.episodic_prune(before)` on a schedule (reuse the background-summary pattern, or fold into the periodic-extraction task).
```python
@work(exclusive=False, thread=False)
async def _run_retention(self) -> None:
    if self._memory is None:
        return
    before = (datetime.now() - timedelta(days=self.settings.memory.retention_days)).isoformat()
    try:
        n = await self._memory.episodic_prune(before)
        logger.info("retention pruned %d episodes", n)
    except Exception:
        logger.exception("retention failed")
```
Call it on mount alongside the other scheduled tasks.
**Files:** `src/gaia/config.py`, `src/gaia/core/memory/{file_store,chroma_store}.py` (unchanged), `src/gaia/gaia_tui_app.py`
**Verify:** Seed episodic events older and newer than the threshold; assert `episodic_prune` deletes only old ones (already covered by `tests/test_file_store.py`/`test_chroma_store.py`).
**⚠ Decision:** retention default (30 days) and whether pruning also affects semantic facts (currently only episodic).

---

## Issue #7 — O(n) full-scan searches
**Severity: HIGH** · *issues.md §4.1*

**Root cause.** Both backends read every file / all documents on every query; the file backend reads every `.md` body on each search, `episodic_prune`, and `episodic_get_conversation`. Chroma falls back to fetch-all-then-filter. Growth compounds the cost.

**Proposed fix — incremental index sidecar for the file backend.** Add a lightweight JSONL/JSON index of metadata only, so metadata-level operations (`episodic_prune`, `episodic_get_conversation`, filtered `episodic_search`) never read all bodies.

In `FileMemoryStore`:
- On `initialize()`, scan `*.md` once and build `self._index: dict[str, tuple[str, dict]]` keyed by `<subdir>/<id>` → `(path, meta)`. Store it as `~/.index.json` next to `memory_dir` (or per-subdir) and reload.
- On `semantic_insert`/`semantic_delete`/`episodic_*`/`procedure_*`, update the in-memory index and persist (append to a JSONL journal, or rewrite the small index file).
- `_scoring_search` / `episodic_prune` / `episodic_get_conversation` filter **via the index** (metadata only), and read bodies only for the matched subset.

Concretely, `episodic_prune` becomes:
```python
async def episodic_prune(self, before_timestamp: str) -> int:
    ids = [eid for eid, (path, meta) in self._index.items() if meta.get("timestamp","") < before_timestamp]
    if ids:
        await asyncio.to_thread(lambda: [Path(p).unlink() for _id, (p, _m) in self._index.items() if _id in ids])
        # purge index entries
    return len(ids)
```

**Files:** `src/gaia/core/memory/file_store.py`
**Verify:** Add tests: (a) prune by timestamp via the index; (b) `episodic_get_conversation` returns correct order; (c) index reload across instances.
**Note (real fix):** For long term, the spec originally intended SQLite+FTS5 as the default file backend. This sidecar index is a stopgap — flag it in the PR as the interim step.
**⚠ Decision:** index format and location (single JSON vs per-subdir JSONL); keep it simple.

---

## Issue #4 — No context-window management
**Severity: HIGH** · *issues.md §4.3*

**Root cause.** `message_history` is forwarded unbounded to the LLM every turn (`acteract`), with no compression or eviction. Long conversations overflow the context window, inflating latency/cost and eventually failing.

**Proposed fix — bounded context window with rollup.** Add a small `ContextWindow` helper (new module `src/gaia/core/context.py`) that tracks a token estimate and, when a budget is exceeded, rolls the oldest half of `message_history` into a single summarized "context" message persisted to episodic memory.

```python
# src/gaia/core/context.py
class ContextWindow:
    """Token-budgeted wrapper around the LLM message history."""
    def __init__(self, max_tokens: int = 4000, summary_min_age_turns: int = 8):
        ...
    @staticmethod
    def estimate(text: str) -> int:
        return max(1, len(text) // 4)  # rough chars/4 ≈ tokens
    def add(self, message: dict) -> bool:
        """Return False if adding would overflow."""
        ...
    async def rollup(self, messages: list[dict], memory: MemoryStore, agent) -> list[dict]:
        """Summarize the oldest half into an episodic 'context summary' and return the trimmed list."""
        ...
```

Wire it into `Gaia`:
- Hold a `ContextWindow` instance on `Gaia`.
- In `acteract`, after streaming, call `window.add(new_message)`. On overflow, call `window.rollup(...)` to summarize the rolled-off half into episodic memory, then reset `message_history` to `[summary, *recent]`.
- Add `max_context_tokens` to config (default 4000 for local models; make configurable).

**Files:** new `src/gaia/core/context.py`, `src/gaia/core/agent/base.py`, `src/gaia/config.py`
**Verify:** Test `ContextWindow.estimate` and `add`/`rollup` against a fake memory store that records the summary; assert `message_history` stays under budget.
**⚠ Decision:** budget (4k tokens is a placeholder for local models; the spec mentions 128k for remote). Rollup prompt/quality.

---

## Issue #9 — Self-modifying system prompt
**Severity: HIGH** · *issues.md §1.2*

**Root cause.** The app loads `AGENT.md` from disk, injects it as instructions, and (via `/settings`) writes it back — but only with a timestamped backup. The prompt claims versioning + approval + rollback that doesn't exist. This is a prompt-injection vector: anything influencing the model could coerce it to rewrite its own guardrails.

**Proposed fix — versioned, settings-owned, rollback-capable prompt store.** Move the canonical system prompt into `settings.json` (the settings modal already edits it) and add a versioned backup manager.

1. Make the settings modal the single source of truth for the prompt; treat the on-disk `AGENT.md` as an optional *seed* (already the case — it's read first, then settings override).
2. Add a `SystemPromptStore` helper (`src/gaia/system_prompt.py`) that, on each write: keeps the last N numbered backups (`AGENT.md.v1`, `.v2`, …), writes a small manifest with timestamp+length, and rejects empty prompts.
```python
# src/gaia/system_prompt.py
_MAX_BACKUPS = 10
def save_system_prompt(text: str) -> None:
    if not text.strip():
        raise ValueError("system prompt cannot be empty")
    # rotate numbered backups down
    for i in range(_MAX_BACKUPS - 1, 0, -1):
        src = Path(f"AGENT.md.v{i}"); dst = Path(f"AGENT.md.v{i+1}")
        if src.exists(): src.rename(dst)
    if Path("AGENT.md").exists():
        Path("AGENT.md").rename("AGENT.md.v1")
    Path("AGENT.md").write_text(text, encoding="utf-8")
```
3. (Optional, higher effort) expose `/prompt-rollback <n>` to restore a numbered backup.

**Files:** new `src/gaia/system_prompt.py`, `src/gaia/slash_commands.py`, `src/gaia/settings_modal.py`
**Verify:** Write the prompt twice and assert numbered backups rotate; assert empty prompt is rejected.
**Note:** Full self-modification approval gating is out of scope for this fix — this hardens storage/versioning. The broader "agent edits its own prompt" design should be explicitly approved by the user (see issues.md #1.2).

---

## Issue #1 — Encryption advertised but unimplemented ⚠ *decision required*
**Severity: CRITICAL** · *issues.md §1.1*

**Root cause.** "SQLCipher Vault / AES-256" is in the UI and `/status`, but there's no encryption dependency and the `encrypted` flag affects nothing — both stores write plaintext.

**This is a product decision, not a code fix.** Two viable paths:

- **Path A — Remove the claim (safe, cheap).** Delete the encryption wording from the header, `/status`, and `/settings` (keep a generic "local, private" line), and drop the `encrypted` toggle or document it as "coming soon." Recommend doing this **now** so shipped behavior matches marketing.
  - *Files:* `src/gaia/gaia_tui_app.py` (header/welcome/status labels), `src/gaia/slash_commands.py` (`/status` vault line), `src/gaia/settings_modal.py` (toggle).
- **Path B — Implement real at-rest encryption.** Choose an auth model first (⚠): derive a key from a user-entered password (prompt at first run) or the OS keychain. Then either (i) encrypt each file store's bytes with AES-256-GCM at the write boundary (small, self-contained — wrap `FileMemoryStore._write_file`), or (ii) switch the default file backend to SQLite + SQLCipher as the spec intends (larger, matches the architecture). Add a `sqlalchemy-cipher`/`pycryptodome` dependency.
  - *Files:* depends on path — `file_store.py` (i) or a new `sql_store.py` (ii); `config.py`/`factory.py` to honor `encrypted`.
  - **⚠ Decision needed:** auth model (password vs keychain), backend (byte-encryption vs SQLite+SQLCipher), and whether to ship Path A now while Path B is built.

**Recommendation:** Ship Path A immediately (remove the false claim), then implement Path B as a tracked feature with a real auth decision before starting.

---

## Lower-severity items (summary — implement as time permits)

| # | Issue | Severity | Effort | Notes |
|---|---|---|---|---|
| 11 | `ARCHITECTURE.md` stale (§7.1) | HIGH | Low | Regenerate from current code; fix "agent not wired" claim. |
| 13 | Broad `except: pass` masking (§2.3) | MEDIUM | Medium | Surface store/agent mismatch; narrow handlers. |
| 14 | Memory injected as instructions (§1.3) | MEDIUM | Low | Delimit retrieved memory as data. |
| 17 | Spec/code drift (§7.2) | MEDIUM | Low | Align spec (backends, SQLCipher, extraction). |
| 15 | Duplicate/`_new`/`_phase` tests (§8.2) | MEDIUM | Low | Consolidate, delete stale. |
| 16 | New modules untested (§8.1) | MEDIUM | Medium | Test streaming loop + slash dispatch. |
| 18 | Frozen `Settings`/`memory_dir` dup (§5.2) | LOW | Low | Centralize memory config; document restart requirement. |
| 19 | Duplicated slash parsing (§5.3) | LOW | Low | Route through `config.parse_slash_command`. |
| 20 | Weak recency-weighted ranking (§4.4) | LOW | Medium | BM25-style scoring for file backend. |

---

## Definition of done (per fix)

- [ ] Change is isolated to the issue (no unrelated reformatting).
- [ ] Existing tests pass: `uv run pytest`.
- [ ] At least one new test covering the fix (or a regression guard for behavioral changes).
- [ ] `ruff` clean: `uv run ruff check .` (or the project's configured linter).
- [ ] PR description references the originating `issues.md` line.

---

*Proposal generated for the `refactor` branch. Blocking (⚠) decisions need a human sign-off before implementation. See [`issues.md`](issues.md) for full detail and severity reasoning.*
