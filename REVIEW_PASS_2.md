# Review Pass 2 — ChromaDB Fetch-All-Then-Fix (Issue #15)

This documents the second round of code review findings and the fixes applied.
All 288 tests pass.

---

## Findings & Fixes

### 1. Import ordering violation
**Finding:** `import logging` was on line 24, after `import chromadb` (third-party). This breaks the stdlib → third-party → local grouping convention.

**Fix:** Moved `import logging` to line 17, grouped with stdlib imports (`asyncio`, `json`, `time`, `uuid`). Deleted the duplicate on line 24.

- [`chroma_store.py:17`](src/gaia/core/memory/chroma_store.py#L17) — `import logging` now in stdlib group
- [`chroma_store.py:24`](src/gaia/core/memory/chroma_store.py#L24) — removed stray import

---

### 2. Missing audit log delete test
**Finding:** `delete_memory("audit", id)` is handled in the match/case but has no test coverage. The `TestDeleteMemory` class only covers semantic, episodic, and procedure.

**Fix:** Added `test_delete_audit` in `TestDeleteMemory`. Creates an audit entry, retrieves its ID via `collection.get()`, deletes it via `delete_memory("audit", id)`, then verifies it's gone.

- [`test_chroma_store.py:603-617`](tests/test_chroma_store.py#L603-L617) — new `test_delete_audit`

---

### 3. Missing combined filter test (event_types + time_range)
**Finding:** Individual filter tests exist (event_types alone, time_range alone) but no test covers the `$and` code path. This is the most common real-world usage and the most complex code path in the where-clause builder.

**Fix:** Added `test_search_combined_filters` in `TestEpisodicMemory`. Creates two events (conversation_turn + tool_call), queries with both `event_types=["conversation_turn"]` and `time_range=("2020-01-01", "2030-01-01")`, verifies only conversation_turn is returned.

- [`test_chroma_store.py:255-268`](tests/test_chroma_store.py#L255-L268) — new `test_search_combined_filters`

---

### 4. `episodic_prune` redundant/incorrect secondary check
**Finding:** The `where={"epoch_ts": {"$lt": epoch_threshold}}` fetches records by numeric epoch, then lines 566–568 re-checked using `ts < before_timestamp` (ISO string, lexicographic comparison). These use different time representations. If the ISO timestamp is in a non-UTC timezone, the string comparison could exclude records the numeric check included.

**Fix:** Removed the secondary string comparison entirely. The epoch filter is authoritative — all documents written by the app use `_now_iso()` (UTC) and `time.time()`, so they're consistent.

> **Also fixed a bug I introduced:** In the original simplification I replaced the delete loop with `return len(result["ids"])`, which returned the count but never actually deleted anything. This broke `test_full_conversation_flow` (which expects prune to remove events). Restored the `collection.delete(ids=ids_to_delete)` call.

- [`chroma_store.py:556-564`](src/gaia/core/memory/chroma_store.py#L556-L564) — prune now returns count after actual delete
- Before: returned count but skipped the delete step (bug)
- Before: had redundant `if ts < before_timestamp` string comparison (correctness risk)

---

### 5. `semantic_delete` vs `delete_memory` inconsistency
**Finding:** `semantic_delete()` called `delete(where={"fact_id": fact_id})` — metadata filter. `delete_memory()` calls `delete(ids=[target_id])` — direct ID delete. Both work because `fact_id` is used as both document ID and metadata field, but the inconsistency means two callers deleting the same fact use different mechanisms. If the metadata field name ever changes (e.g. `fact_id` → `entry_id`), `semantic_delete` would silently fail.

**Fix:** Changed `semantic_delete` to use `delete(ids=[fact_id])` to match `delete_memory`.

- [`chroma_store.py:237-241`](src/gaia/core/memory/chroma_store.py#L237-L241) — `semantic_delete` now uses `ids=`

---

## Files Changed

| File | Lines | Changes |
|------|-------|---------|
| [`src/gaia/core/memory/chroma_store.py`](src/gaia/core/memory/chroma_store.py) | 17, 23–24, 237–241, 556–564 | Import order, `semantic_delete` fix, prune fix |
| [`tests/test_chroma_store.py`](tests/test_chroma_store.py) | 255–268, 603–617 | 2 new tests (combined filter, audit delete) |

## Test Results

**288 tests pass** (up from 286 — added 2 new tests).
