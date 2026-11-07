# Code Review Findings — Post-Merge Audit

**Date:** 2025-07-15
**Source:** Multi-angle code review after `main` → `containerization` merge
**Scope:** Full repo across 8 review dimensions (correctness, security, performance, testing, architecture)

---

## Severity key

| Level | Meaning |
|-------|---------|
| **🔴 Critical** | Data loss, silent corruption, security bypass |
| **🟠 High** | Broken functionality, race conditions, degraded correctness |
| **🟡 Medium** | Performance degradation, maintenance hazards, test gaps |
| **🟢 Low** | Dead code, style inconsistencies, minor improvements |

---

## 1. ChromaDB `delete_memory` uses `where` clause instead of direct ID lookup

**File:** `src/gaia/core/memory/chroma_store.py:651`
**Severity:** 🔴 Critical
**Finding:** For semantic fact deletion, the code calls `collection.delete(where={'fact_id': target_id})` instead of `collection.delete(ids=[target_id])`.

**Failure scenario:** If the collection metadata schema changes (e.g. `fact_id` renamed to `entry_id`), the `where` filter silently matches zero documents. The fact remains in ChromaDB and continues to be returned by `semantic_search`, polluting the agent's context with stale/deleted facts. No error is raised.

**Suggested fix:** Use `ids=[target_id]` for all delete operations. The `where` clause should only be used for filtering-based deletes, not identity-based ones.

```python
# Instead of:
collection.delete(where={'fact_id': target_id})

# Use:
collection.delete(ids=[target_id])
```

**Status:** ✅ **Resolved** — [`4cbce49`](https://github.com/GeorgeDittmar/gaia/commit/4cbce49)
Both `delete_memory` (line 651) and `semantic_delete` (line 225) now use `collection.delete(ids=[...])`. A pre-existing bug was caught during review: `semantic_similarity_search` hardcoded `"semantic_facts"` as the collection key instead of using `self._col("semantic")`, which would crash with `KeyError`. That was fixed in the same commit.

---

## 2. `generate_user_summary` LLM call has no back-off or contention guard

**File:** `src/gaia/core/agent/base.py:520`
**Severity:** 🟠 High
**Finding:** The `generate_user_summary` method calls `summary_agent.run()` without the `asyncio.sleep(2)` delay that `_extract_facts` uses as a guard.

**Failure scenario:** When the user sends a message and the main agent starts streaming, the background summary timer (10-min interval) fires simultaneously. Both the main agent and summary agent call the local LLM endpoint. For local backends with a single model slot (Ollama, llama.cpp), one call times out or returns garbled output. This is a guaranteed contention scenario.

**Suggested fix:** Add a 2-second delay before the summary agent's LLM call, matching the pattern in `_extract_facts`.

```python
async def generate_user_summary(self):
    await asyncio.sleep(2)  # Wait for main response to complete
    # ... rest of method
```

**Status:** ⚠️ **False positive** — the `asyncio.sleep(2)` guard already exists at line 520 with the comment "Brief delay to avoid competing with the main streaming API call".

---

## 3. File store `_build_index` only indexes episodic entries

**File:** `src/gaia/core/memory/file_store.py:291`
**Severity:** 🟡 Medium
**Finding:** `_build_index` only populates index keys for entries that have an `event_id` (episodic memories). Semantic facts, procedures, and audit entries are entirely unindexed.

**Failure scenario:** User has 500 saved semantic facts. A `semantic_search('python')` call reads and parses all 500 `.md` file bodies to compute keyword overlap. The promised O(1) index-sidecar optimization is never used for the most common query path. Search latency degrades linearly as the fact store grows.

**Suggested fix:** Index all entry types in `_build_index`, not just episodic entries. Semantic facts should be indexable by their content tags and keywords.

**Status:** ✅ **Resolved** — [`2d975e0`](https://github.com/GeorgeDittmar/gaia/commit/2d975e0)
`_build_index` now covers all entry types (semantic, procedure, audit, episodic). `semantic_search` and `procedure_search` pre-filter from the index. Audit entries are immediately indexable after `audit_log()`. Dead `_persist_index()` code was removed (it was never loaded, only written as a side effect).

---

## 4. Periodic extraction bypasses Textual `@work` task lifecycle

**File:** `src/gaia/gaia_tui_app.py:674`
**Severity:** 🟡 Medium
**Finding:** `_run_periodic_extraction` is decorated with `@work(exclusive=False)`, making it a Textual background worker. But it's scheduled via `asyncio.get_event_loop().call_later(5, self._run_periodic_extraction)`, which passes it as a plain synchronous callback.

**Failure scenario:** When `call_later` invokes the method, the `@work` decorator returns a `TaskType` but `call_later` discards it. The async task may still run, but it bypasses Textual's worker lifecycle management — if the app is shutting down, the task may not be properly cancelled, or `call_later` itself may raise `RuntimeError` if the event loop is already closed.

**Suggested fix:** Use `self.run_in_background()` or `asyncio.create_task()` instead of `call_later` for scheduling `@work` methods. Or restructure the scheduler to use `asyncio.sleep` in an async loop managed by `@work`.

**Status:** ✅ **Resolved** — [`71e63cf`](https://github.com/GeorgeDittmar/gaia/commit/71e63cf)
Replaced `call_later` with `_periodic_extraction_loop()` — an `@work` async method that runs a `while not self.is_shutting_down` loop with `asyncio.sleep(1)` steps (300 iterations = 5 min). The `is_shutting_down` guard is consistent with `ping_loop`'s pattern. All `call_later` references removed from `run_agent_execution` as well.

---

## 5. Redundant `hasattr` check on name-mangled attribute in `reconfigure`

**File:** `src/gaia/core/agent/base.py:513`
**Severity:** 🟢 Low
**Finding:** `reconfigure` has `if hasattr(self, '_Gaia__user_summary'):` — this check is always `True` because `__init__` unconditionally sets `self.__user_summary`.

**Failure scenario:** Dead code. If someone renames the attribute without updating this check, `hasattr` returns `False`, the user summary is never appended to instructions, and the agent silently loses all user profile context. The degradation is silent — no error, no warning.

**Suggested fix:** Remove the `hasattr` guard. The attribute is unconditionally set in `__init__`.

```python
# Instead of:
if hasattr(self, '_Gaia__user_summary'):
    instructions.append(f"\n## User Profile\n{self.__user_summary}")

# Use:
instructions.append(f"\n## User Profile\n{self.__user_summary}")
```

**Status:** ✅ **Resolved** — [`c511974`](https://github.com/GeorgeDittmar/gaia/commit/c511974)
Guard was in `generate_user_summary` (line 486), not `reconfigure`. Removed — attribute is always set in `__init__`.

---

## 6. Two incompatible `_parse_frontmatter` implementations

**Files:** `src/gaia/core/memory/file_store.py:48` and `src/gaia/core/agent/base.py`
**Severity:** 🟡 Medium
**Finding:** Two separate implementations of `_parse_frontmatter` exist:

| Aspect | `file_store.py` | `base.py` |
|--------|-----------------|-----------|
| Source | `import yaml` | Regex-based manual parsing |
| `null` | `None` | Literal `"null"` |
| `true`/`false` | `True`/`False` | Literal `"true"`/`"false"` |
| Numbers | `int`/`float` | Literal `"42"` |
| `confidence` | `0.95` (float) | `"0.95"` (str) |

`slash_commands.py` imports from `base.py` (string-only version).

**Failure scenario:** If downstream code anywhere expects numeric types from frontmatter parsing (e.g. `if entry["confidence"] > 0.8:`), it will fail with `TypeError` when using the `base.py` parser, which returns `"0.8"` as a string. The two implementations also diverge on edge cases — `null` is `None` in one and the literal string `"null"` in the other.

**Suggested fix:** Consolidate to a single implementation. The `yaml`-based version in `file_store.py` is more robust. Make it the canonical one and remove the regex version from `base.py`.

**Status:** ✅ **Resolved** — [`50a8b26`](https://github.com/GeorgeDittmar/gaia/commit/50a8b26)
Removed the regex-based parser from `base.py`. Both `base.py` and `slash_commands.py` now import from `file_store.py`. Type coercion (`null`→`None`, `true`→`True`, numbers→int/float) is now consistent across all consumers.

---

## Summary

| # | File | Severity | Status | Commits |
|---|------|----------|--------|---------|
| 1 | `chroma_store.py:651` | 🔴 Critical | ✅ Resolved | `4cbce49` |
| 2 | `base.py:520` | 🟠 High | ⚠️ False positive | — |
| 3 | `file_store.py:291` | 🟡 Medium | ✅ Resolved | `2d975e0` |
| 4 | `gaia_tui_app.py:674` | 🟡 Medium | ✅ Resolved | `71e63cf` |
| 5 | `base.py:486` | 🟢 Low | ✅ Resolved | `c511974` |
| 6 | `file_store.py:48` / `base.py` | 🟡 Medium | ✅ Resolved | `50a8b26` |

**5 of 6 issues resolved across 5 commits. 1 issue was a false positive (guard already existed).**
