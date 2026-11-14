# Code Review — `containerization` branch
**Date:** 2025-07-12
**Branch:** `containerization` (ahead of `main` by ~5 commits; recent: warmup/greeting streaming, CSS fixes)
**Files under review:** `core/agent/base.py`, `gaia_tui_app.py`, `core/memory/sqlite_store.py`, `core/memory/chroma_store.py`, `core/memory/file_store.py`

---

## Critical bugs — **FIXED**

### 1. `base.py:273` — `datetime.timestamp` is the unbound method descriptor, not a timestamp ⚑

```python
# base.py:273 (before)
"Summary Data as of "+ str(datetime.timestamp)
```

`datetime.timestamp` (on the **class**, not an instance) is the descriptor `<method 'timestamp' of 'datetime.datetime' objects>`. `str(datetime.timestamp)` produces that literal string, which got baked into the agent's system prompt instructions, corrupting the LLM's prompt.

**Fixed:** `datetime.now().isoformat()` — produces a proper ISO 8601 timestamp like `2025-07-12T09:45:00.123456`.

**Commit:** [`9e6ccdb`](https://github.com/GeorgeDittmar/gaia/commit/9e6ccdb)

---

### 2. `gaia_tui_app.py:652` — `indicator.render_str` is a method, never cycles animation states ⚑

```python
# gaia_tui_app.py:652 (before)
current = indicator.render_str
if "WARMING..." in current:
    ...
elif "WARMING." in current and ".." not in current:
    ...
else:
    ...
```

`render_str` is a **bound method** (`<bound method Static.render_str of ...>`). Assigning it without calling `()` gave the method object, not the rendered string. All three `in` checks compared against the method's `__repr__`, never matching, so every call fell through to the `else` branch and the indicator stayed locked on `WARMING...`.

**Fixed:** Added `self._warming_phase: int = 0` instance variable in `__init__`, replaced `_animate_warming` with `match`/`case` cycling 0→1→2→0 via `(phase + 1) % 3`.

**Commit:** [`2aa03fe`](https://github.com/GeorgeDittmar/gaia/commit/2aa03fe)

---

## Medium bugs — **FIXED**

### 3. `base.py:44` — `_USER_SUMMARY_PATH` hardcodes `~/.gaia`, ignores `GAIA_HOME` ⚑

```python
# base.py:44 (before)
_USER_SUMMARY_PATH = Path.home() / ".gaia" / "user_summary.md"
```

All other paths in the codebase use `SETTINGS_DIR` / `GAIA_HOME` from `gaia.config`, which respects the `GAIA_HOME` environment variable set by Docker/Podman. In container deployments with `GAIA_HOME=/home/gaia/data`, the summary file landed at `~/.gaia` on the **host** (outside the bind mount), so it was lost across container restarts.

**Fixed:** Added `from gaia.config import SETTINGS_DIR`, changed path to `SETTINGS_DIR / "user_summary.md"`. Verified `GAIA_HOME=/tmp/test-gaia` propagates correctly.

**Commit:** [`e8e069e`](https://github.com/GeorgeDittmar/gaia/commit/e8e069e)

---

## Low-priority bugs — **FIXED**

### 4. `sqlite_store.py:531` — Substring counting masquerading as token overlap ⚑

```python
# sqlite_store.py:531 (before)
score = sum(1 for kw in keywords if kw in text)
```

`kw in text` is a **substring** check, not a token-set check. The keyword `"for"` matches inside `"information"`, `"is"` matches inside `"this"`. Common stop words caused false-positive duplicate detection.

**Fixed:** Replaced with proper token set intersection: `all_tokens = set(keywords)` and `text_tokens = set(text.split())`, comparing `len(all_tokens & text_tokens) >= max(1, len(all_tokens) * distance_threshold)`.

**Commit:** [`3809716`](https://github.com/GeorgeDittmar/gaia/commit/3809716)

---

### 5. `chroma_store.py:298` — Cosine distance threshold 0.8 too loose ⚑

```python
# chroma_store.py:295-298 (before)
# Threshold 0.8 means "anything with cosine distance <= 0.8 is considered duplicate"
return any(d <= distance_threshold for d in distances[0])
```

Cosine distance 0.8 = cosine similarity 0.2 — only a 37° angle. Unrelated facts with default-ish embeddings could have cosine similarity 0.3–0.5 (distance 0.5–0.7) and get silently deduplicated.

**Fixed:** Lowered default `distance_threshold` from `0.8` to `0.3` across chroma_store, sqlite_store, and the protocol base class. Updated stale comment and docstring.

**Commit:** [`3809716`](https://github.com/GeorgeDittmar/gaia/commit/3809716)

---

### 6. `file_store.py:392` — Jaccard threshold 0.8 too strict ⚑

```python
# file_store.py:390-392
jaccard = len(intersection) / len(union) if union else 0.0
if jaccard >= distance_threshold:
    return True
```

Jaccard 0.8 requires near-identical token sets — essentially identical short sentences. Two facts like "User is building a Python TUI with Textual" vs "User is building a Textual-based TUI app" would tokenize differently and slip through. For short facts (10–30 tokens), even rephrasing drops Jaccard below 0.5.

**Fixed:** Lowered default from `0.8` to `0.5`. (Jaccard is a proper 0–1 similarity ratio, so 0.5 means "at least 50% token overlap" — appropriate for short text dedup.)

**Commit:** [`3809716`](https://github.com/GeorgeDittmar/gaia/commit/3809716)

---

### 7. `base.py:724` — Conflict detection before dedup with higher threshold ⚑

```python
# base.py:722-751 (before)
# 1. Conflict check (default threshold 0.8)
conflict = await self._detect_facts_conflict(fact_text)
# 2. Dedup check (threshold 0.75)
if await self._memory.semantic_similarity_search(fact_text, ..., distance_threshold=0.75):
```

A fact at distance 0.76 wouldn't trigger conflict (below 0.8) and wouldn't trigger dedup (above 0.75). It got saved as a "new" fact even though it was arguably similar.

**Fixed:**
1. Swapped order: dedup check first, conflict detection second (a duplicate should never reach conflict).
2. Added explicit `distance_threshold=0.75` to `_detect_facts_conflict`'s `semantic_search` call so both checks use the same threshold.

**Commit:** [`3809716`](https://github.com/GeorgeDittmar/gaia/commit/3809716)

---

### 8. `gaia_tui_app.py:683-684` — 300ms interval frozen during LLM I/O ⚑

```python
# gaia_tui_app.py:683-685
self._warming_timer = self.set_interval(
    0.3, self._animate_warming
)
```

During warmup, the event loop may be blocked by network I/O. `set_interval` callbacks don't fire when blocked, so the animation freezes on a single frame.

**Fixed:** Replaced counter-based `_warming_phase` with wall-clock time tracking. `self._warming_start = time.time()` in `_warmup_llm`, animation phase = `int(elapsed * 3) % 3`. Animation advances based on real elapsed time even when the loop is blocked.

**Commit:** [`3809716`](https://github.com/GeorgeDittmar/gaia/commit/3809716)

---

## Summary

| # | File | Line | Severity | Status | Description |
|---|------|------|----------|--------|-------------|
| 1 | `core/agent/base.py` | 273 | **Critical** | ✅ Fixed | `datetime.timestamp` was an unbound method descriptor, corrupted system prompt |
| 2 | `gaia_tui_app.py` | 652 | **Critical** | ✅ Fixed | `render_str` was a method reference; animation stuck on `WARMING...` |
| 3 | `core/agent/base.py` | 44 | Medium | ✅ Fixed | Hardcoded `~/.gaia` ignored `GAIA_HOME`; summary lost in containers |
| 4 | `core/memory/sqlite_store.py` | 531 | Low | ✅ Fixed | Substring `in` check → proper token set intersection |
| 5 | `core/memory/chroma_store.py` | 298 | Low | ✅ Fixed | Cosine distance threshold 0.8 → 0.3 |
| 6 | `core/memory/file_store.py` | 392 | Low | ✅ Fixed | Jaccard threshold 0.8 → 0.5 |
| 7 | `core/agent/base.py` | 724 | Low | ✅ Fixed | Swapped dedup/conflict order, unified threshold |
| 8 | `gaia_tui_app.py` | 683 | Low | ✅ Fixed | Wall-clock animation instead of callback counter |

**Status:** All 8 findings fixed and pushed. Total commits: `9e6ccdb`, `2aa03fe`, `e8e069e`, `3809716`. All 262 tests pass across all changes.
