# User Summary Feature — Review Issues

## Must Fix

### 1. `/summary` displays raw file content including frontmatter

**File:** `src/gaia/slash_commands.py` (the `case "summary":` handler)

The handler reads the file directly and displays full contents including YAML frontmatter:

```
---
version: 1
last_updated: 2025-07-23T12:00:00
facts_count: 12
conversation_count: 5
---

# User Summary
...
```

Meanwhile, the agent's internal `_load_user_summary()` correctly strips frontmatter via `_parse_frontmatter()` before returning the body.

**Fix:** Strip frontmatter before displaying. Import `_parse_frontmatter` from `gaia.core.agent.base`:

```python
from gaia.core.agent.base import _parse_frontmatter
...
content = summary_path.read_text(encoding="utf-8")
_, content = _parse_frontmatter(content)
```

---

### 2. `_save_user_summary` hardcodes metadata to always-zero values

**File:** `src/gaia/core/agent/base.py` lines 297-302

```python
metadata = {
    "version": "1",
    "last_updated": now,
    "facts_count": "0",
    "conversation_count": "0",
}
```

`facts_count` and `conversation_count` are perpetually `"0"`. They are written to disk on every save but never updated to reflect reality.

**Fix:** Use the actual counts from the data that was gathered:

```python
metadata = {
    "version": "1",
    "last_updated": now,
    "facts_count": str(len(semantic_facts)),
    "conversation_count": str(len(planner_notes)),
}
```

---

## Should Fix

### 3. `SlashCommandHandler` accesses name-mangled private attribute

**File:** `src/gaia/slash_commands.py`

The handler directly accesses `_GaiaTUIApp__core_agent`, which is Python's name-mangled form of `GaiaTUIApp.__core_agent`. This couples the handler to an internal implementation detail. If the attribute is ever renamed, this silently breaks.

The existing codebase already has the same pattern for `extract` and `extract-debug` commands, so this is a pre-existing design smell. But the new summary commands replicate it, doubling the surface area.

**Fix:** Expose a property on `GaiaTUIApp`:

```python
@property
def core_agent(self) -> Gaia:
    return self._GaiaTUIApp__core_agent
```

Then `slash_commands.py` uses `self._app.core_agent`.

---

### 4. `generate_user_summary` swallows all errors silently

**File:** `src/gaia/core/agent/base.py` lines 439-441

```python
except Exception:
    logger.exception("user summary generation failed")
    return ""
```

When the LLM is unreachable, the network times out, or the summary agent crashes, the method returns `""`. The slash command handler then displays:

```
Summary regenerated (empty — no facts to summarize yet)
```

This is misleading — the user may have facts, but the LLM was down. The caller cannot distinguish "nothing to summarize" from "summarization failed."

**Fix:** Let the exception propagate and let the `@work` wrapper in `_run_periodic_summary` catch it. The slash command handler already mounts a status message before calling `generate_user_summary()`, so a failure would be logged and the status message can be updated. Remove the bare `except Exception` in `generate_user_summary()`.

---

### 5. File read in `/summary` has no error handling

**File:** `src/gaia/slash_commands.py` (the `case "summary":` handler)

```python
content = summary_path.read_text(encoding="utf-8")
```

If the file exists (the `exists()` check passes) but cannot be read — permissions issue, disk error, file deleted between `exists()` and `read_text()` — this raises and the command crashes with an unhandled exception.

**Fix:** Wrap in try/except:

```python
try:
    content = summary_path.read_text(encoding="utf-8")
except OSError:
    await chat_box.mount(ChatTurn(
        "[bold #ff007f]Error:[/bold #ff007f] Could not read summary file.",
        classes="agent-msg",
    ))
    return
```

---

## Minor / Observations

### 6. `reconfigure()` uses redundant `hasattr` check

**File:** `src/gaia/core/agent/base.py` line 335

After `__init__` runs, `self.__user_summary` always exists. The `hasattr(self, "_Gaia__user_summary")` check is always True and can be removed for clarity.

### 7. No `asyncio.sleep(2)` delay before summary agent LLM call

**File:** `src/gaia/core/agent/base.py` `generate_user_summary()`

The `_extract_facts()` method has `await asyncio.sleep(2)` to avoid competing with the main streaming API call. The summary agent call (`summary_agent.run()`) has no such delay. If the summary generation is triggered right after a streaming response, it could contend with the main agent for the LLM endpoint.

**Fix:** Add `await asyncio.sleep(2)` before calling `_make_summary_agent().run()` in `generate_user_summary()`.

### 8. `_SUMMARIZE_EVERY_N_TURNS = 5` may generate summaries too aggressively

Every 5 turns triggers a full LLM call to regenerate the summary. Combined with the existing `_EXTRACT_EVERY_N_TURNS = 3` (fact extraction), this means the LLM is called for summary generation on every 5th turn regardless of whether there's new data.

**Consider:** Only trigger summary generation when the last summary is older than N turns, or when `generate_user_summary()` has data to work with. The current implementation already returns early if there's no data (line 411-413), so this may be fine — just flagging that the turn counter still fires and logs even for empty summaries.

---

## Summary

| # | Severity | Description |
|---|----------|-------------|
| 1 | **Must fix** | `/summary` shows frontmatter to user, inconsistent with agent behavior |
| 2 | **Must fix** | `_save_user_summary` hardcodes `"facts_count": "0"` and `"conversation_count": "0"` |
| 3 | **Should fix** | `SlashCommandHandler` accesses name-mangled private `__core_agent` |
| 4 | **Should fix** | `generate_user_summary` swallows all errors, caller cannot distinguish failure from empty |
| 5 | **Should fix** | File read in `/summary` has no error handling |
| 6 | **Minor** | `reconfigure()` uses redundant `hasattr` check |
| 7 | **Minor** | No `asyncio.sleep(2)` delay before summary agent LLM call |
| 8 | **Minor** | Summary generation fires on every 5th turn even with no data (cosmetic logging issue) |
