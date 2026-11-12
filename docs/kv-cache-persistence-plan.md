# KV Cache Persistence Plan

## Goal

Persist the LLM's attention state (KV cache) to disk so that on server restart, the model has already "attended to" the full system prompt + user summary without re-processing. Combined with distilled session summaries, this reduces the first-turn latency after a restart and keeps the model "oriented" across sessions.

---

## Components

### 1. KV Cache Save/Load (llama.cpp server side)

**What:** Configure llama.cpp to persist the KV cache for the system prompt prefix to disk.

**How:** Add `--prompt-cache system_cache.bin` to the llama.cpp server startup command.

```bash
llama-server -hf unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q8_K_XL \
  --host 0.0.0.0 --port 8080 --flash-attn on -t 16 -ngl 99 \
  --cont-batching --cache-type-k q8_0 --cache-type-v q8_0 \
  --cache-ram 2048 --spec-type ngram-mod -dt 4 \
  --prompt-cache system_cache.bin
```

**Behavior:**
- First run: KV cache is built in RAM, then dumped to `system_cache.bin` on shutdown
- Subsequent runs: cache loaded from disk, system prompt tokens already processed
- If the prompt changes, the cache becomes stale — llama.cpp ignores it and rebuilds
- File size: ~2-4 GB for a 32K context at q8_0 precision (scales with context, not model size)

**Location:** The file lives in the directory where the server process is started.

### 2. System Prompt Cache File

**What:** Gaia writes the exact system prompt to a file so the cache can be validated.

**File:** `~/.gaia/system_cache_meta.json`

```json
{
    "prompt_hash": "sha256_of_full_system_prompt",
    "updated_at": "2025-07-15T20:30:00Z"
}
```

**Why:** When AGENT.md or the system prompt changes (via `/system_prompt` or direct edit), we delete the cache file so the server doesn't try to use a stale cache.

### 3. Session Summary Extraction

**What:** On shutdown, extract a distilled summary of the last session's conversation and append it to the system prompt.

**When:** During app shutdown (`__on_memory_init_done` callback or a `on_shutdown` hook).

**How:**
1. Query episodic memory for recent conversation turns (last N entries)
2. Send them to the LLM via a compacting prompt:
   ```
   Summarize the following conversation turns into a concise
   context note. Focus on facts, decisions, and unresolved threads.
   ```
3. Append the result to the system prompt as a `## Recent Session` section

**Example output:**
```markdown
## Recent Session
- Discussed ChromaDB migration from SQLite memory backend
- Switched backend to chroma in settings.json
- Resolved /extract-debug widget collision bug
- Confirmed semantic + episodic memory working with ChromaDB
- User wants to explore prompt caching with llama.cpp
```

**Where it lives:** In the system prompt text (prepended to every request), which means it gets cached in the KV cache alongside the rest of the system prompt.

### 4. Gaia Integration

**What:** Wire the cache management into the Gaia app lifecycle.

**Startup (`on_mount`):**
1. Check if `system_cache.bin` exists
2. Check if `system_cache_meta.json` matches the current system prompt
3. If cache is valid, the server already has the prefix cached — no warmup needed
4. If cache is stale or missing, fire a warmup message

**Warmup (if no cache):**
1. On app start, send an empty message to the LLM
2. This triggers one forward pass over the system prompt, building the KV cache in RAM
3. First real user message then skips the prefix re-computation (~20-50% faster)

**Shutdown:**
1. If episodic memory exists, extract a session summary
2. Append it to the system prompt
3. Server automatically dumps KV cache to disk on shutdown

**System prompt changes:**
1. When AGENT.md changes or `/system_prompt` is called
2. Invalidate the cache file (`rm ~/.gaia/system_cache.bin`)
3. Write new hash to `system_cache_meta.json`

---

## File Changes

| File | Change |
|------|--------|
| `~/.gaia/system_cache.bin` | KV cache file (created by llama.cpp) |
| `~/.gaia/system_cache_meta.json` | Prompt hash for cache validation |
| `src/gaia/gaia_tui_app.py` | Add cache validation + warmup on mount, summary extraction on shutdown |
| `src/gaia/fact_extraction.py` | Add `compact_conversation()` method for session summarization |

---

## Risks

| Risk | Mitigation |
|------|-----------|
| Stale cache after prompt change | Hash-based invalidation, delete on change |
| KV cache file grows large with context | `--cache-type-k q8_0` keeps it ~2-4GB; document for users with limited RAM |
| Session summary grows over time | Cap at N turns; discard oldest summaries on overflow |
| Cache only works with llama.cpp, not Ollama | Graceful degradation — fall back to warmup |
| Server must be restarted to benefit | Cache is loaded on server restart, not app restart |

---

## Implementation Order

1. **Add `--prompt-cache` to llama.cpp startup** — manual step, gets the cache working
2. **Add `system_cache_meta.json`** — track prompt hash
3. **Add warmup on app start** — fires empty message if no valid cache
4. **Add session summary extraction on shutdown** — distilled context for next session
5. **Wire it all together** — automatic cache invalidation on prompt change

---

## Estimation

- Step 1: Config change, no code
- Step 2-3: ~50 lines, 30 minutes
- Step 4: ~80 lines, 45 minutes
- Step 5: ~40 lines, 30 minutes

Total: ~200 lines, ~2 hours.
