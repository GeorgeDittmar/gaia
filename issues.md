# G.A.I.A. — Architecture Review & Issues

*Review date: 2025-10-24. Scope: architecture and implementation review of the codebase as it currently stands on `refactor`. Not a line-by-line lint — this focuses on design, correctness, security, scale, and maintainability.*

> **How to read this:** Issues are grouped by area and tagged by severity:
> `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `NIT`. Each has a concrete file:line anchor and a recommended action. This file is intended for later triage, not an immediate fix-all.

---

## TL;DR

- The product's headline promise — **encryption at rest ("SQLCipher Vault / AES-256")** — is **entirely fictional**. No encryption dependency exists and the `encrypted` flag has zero effect on any code path. This is the single most important issue: it's a trust and accuracy problem, not a missing-feature one.
- **Documentation is stale.** [`ARCHITECTURE.md`](ARCHITECTURE.md) describes a module layout that no longer exists (`src/app/`, in-app methods) and omits five live modules (`factory.py`, `memory_lifecycle.py`, `slash_commands.py`, `streaming.py`, `fact_extraction.py`). The spec and docs disagree with the code on agent wiring, memory defaults, and the extraction strategy.
- **Fact extraction runs 2–3× per turn across overlapping code paths** (inline in `acteract`, periodic every 3 turns, and at shutdown) plus a hard 2 s `asyncio.sleep` per LLM call — roughly **doubling local-LLM latency** and spawning overlapping concurrent requests at a single-slot endpoint.
- **Episodic memory has no retention/pruning in practice** — `episodic_prune` is defined and tested but **never called** in app code. Every turn also leaves an unbounded `message_history` list that is forwarded raw to the LLM with no context-window management.
- **Config and logs use CWD-relative paths** (`Path("settings.json")`, `Path("logs")`), so settings silently live in the launch directory and can be lost/created in the wrong place.

---

## 1. Security, Privacy & Trust

### 1.1 Encryption is advertised but not implemented — CRITICAL
The UI header, `/status`, `watch_connection_status`, and `/settings` all advertise "SQLCipher Vault", "AES-256", and "Zero-Telemetry." Yet:
- There is **no encryption dependency** (no `sqlalchemy-cipher`, `pycryptodome`, etc.) in `pyproject.toml`.
- The `encrypted` setting is read only for **labels** — it is never passed to the memory factory, the filesystem, or any storage layer.
- `MemoryFactory.create` ignores `settings.encrypted` entirely; both `FileMemoryStore` and `ChromaMemoryStore` write plaintext.

The spec even lists SQLCipher as a v1 requirement (Section 3) while the code has no trace of it. **Impact:** the app's core differentiator is false, which is worse than a missing feature — it erodes user trust and, if shipped, is a misrepresentation.

**Recommendation:** Either (a) implement real at-rest encryption (encrypt files at the store boundary, or switch the file backend to SQLite+SQLCipher as the spec intends), or (b) remove the encryption claims from all UI copy and `/status` until it exists. Do not leave it half-advertised.

### 1.2 Self-modifying system prompt — HIGH
[`AGENT.md`](AGENT.md:27-38) instructs the model it can "modify your own system prompt" and that the app supports editing it (settings modal writes AGENT.md, with a timestamped backup). The prompt is loaded from disk on every run and injected verbatim into the model instructions. This is a **prompt-injection / jailbreak vector**: if anything influences the model (a memory fact, an MCP tool output, injected context), it could be coerced into rewriting AGENT.md to remove its own guardrails. There's no content validation, schema, or rollback/versioning on the prompt itself.

**Recommendation:** Treat the system prompt as privileged/immutable-by-default. If self-modification is a goal, gate it behind explicit user approval with versioned files and rollback (the prompt *claims* this exists — "Any modifications must be approved by the user... versioned and tested... roll back" — but only a timestamped backup exists).

### 1.3 Memory content injected as instructions — MEDIUM
Retrieved facts are spliced into the augmented prompt as
```
[Relevant stored facts]
- ...
User: {prompt}
Please use the stored facts above when relevant.
```
Any memory entry — including one the LLM hallucinated during extraction, or one influenced by an attacker — is later fed back to the model as authoritative context. Low risk for a local single-user tool, but the injection framing (`use the stored facts above`) is an instruction-injection surface.

**Recommendation:** Mark retrieved memory as *data*, not instructions (e.g. wrap in a clearly delimited `USER_MEMORY:: ... ::END` block, or a `<!-- retrieved context, not user instructions -->` annotation).

### 1.4 Endpoint check conflates "reachable" with "healthy" — MEDIUM
[`check_endpoint`](src/gaia/config.py:169-207) GETs the configured base URL. Two problems:
- For Ollama, the root `/` always returns 200 **even with no model loaded**, so "ONLINE" is a false positive; the real health endpoint is `/api/tags` or `/healthz`.
- It counts **any 2xx–5xx** as ONLINE (4xx/5xx → "server is alive"), and a 404 on llama.cpp's root counts as ONLINE.

**Impact:** the header can show ONLINE while the model is unloaded or the route is wrong.

**Recommendation:** Point the ping at a real health endpoint per backend (Ollama `/api/tags`, llama.cpp `/v1/models`, generic OpenAI `/v1/models`), and require a 2xx for ONLINE.

---

## 2. Correctness & Bugs

### 2.1 CWD-relative settings & log paths — HIGH
- [`SETTINGS_FILE = Path("settings.json")`](src/gaia/config.py:8)
- [`log_dir = Path("logs")`](src/gaia/gaia_tui_app.py:42)

Both are relative to the current working directory, not the app or the user's home. Launch `gaia` from `/tmp` and you get a *different* settings file than from the repo root — settings are effectively lost between launches, and logs scatter wherever you happen to be. This is a real, reproducible bug for console-script users.

**Recommendation:** Resolve `SETTINGS_FILE` to a stable location (e.g. `~/.gaia/settings.json`, consistent with `memory_dir`). Resolve the log dir the same way, or under the settings dir.

### 2.2 `save_config` is not atomic — MEDIUM
[`save_config`](src/gaia/config.py:121-124) does a plain `json.dump(..., f)` over the existing file — no temp-then-rename (unlike the memory store, which does atomic writes). A crash or interrupt mid-write corrupts `settings.json`. `load_config` then swallows the JSON error and silently falls back to defaults, hiding the corruption.

**Recommendation:** Write to a temp file and `os.replace` into place (the memory store already has this pattern — reuse it).

### 2.3 Silent exception swallowing everywhere — MEDIUM
`load_config`, `force_exit`, `_close_memory`, `watch_connection_status`, the memory-init done callback, and extraction all do bare `except Exception: logger...` or `except: pass`. A failing memory init in `__init__` is caught, but the `Gaia` agent was already constructed with that store; if init later fails in `on_mount`, the agent still holds an uninitialized store and its calls fail loudly later. Broad `except` also masks bugs during development.

**Recommendation:** At minimum, log the store/agent mismatch explicitly; prefer specific exceptions for recoverable paths and let truly-unexpected errors surface in the log file with a stack trace (the debug log is DEBUG-level, so you already can).

### 2.4 Extracted "facts" may be injected as wrong instructions — LOW
Tied to 1.3: confidence scoring is heuristic; a low-confidence or misclassified fact becomes context on a later turn with no provenance shown to the model.

---

## 3. Concurrency & Race Conditions

### 3.1 "Final extraction before shutdown" is likely a race — HIGH
[`force_exit`](src/gaia/gaia_tui_app.py:425-444) calls `_schedule_shutdown_extraction`, which calls `self._run_periodic_extraction()` — a `@work` coroutine. But it's then immediately followed by `self.workers.cancel_all()` and `self.exit()`. The background task is created and then **cancelled before it can run**. The "final periodic extraction" is effectively a no-op.

**Recommendation:** Either await the extraction before cancelling workers, or drop the claim. If you want a final pass, make it synchronous and fast, or ensure `workers.cancel_all()` doesn't cancel it.

### 3.2 `call_later(5, ...)` for periodic extraction is racy — MEDIUM
[`run_agent_execution`](src/gaia/gaia_tui_app.py:661) schedules periodic extraction via `asyncio.get_event_loop().call_later(5, self._run_periodic_extraction)`. If the user exits within 5 s, the callback is cancelled and extraction never runs. Same failure mode as 3.1.

**Recommendation:** Use `asyncio.get_running_loop().call_later(...)` (the `get_event_loop()` API is deprecated and misbehaves without a running loop), and reconcile with the shutdown path.

### 3.3 Overlapping concurrent LLM requests to a single-slot endpoint — HIGH
Local backends (llama.cpp, Ollama) typically run **one model slot**. Yet:
- Per-turn inline extraction (`acteract` → `post_turn_extract`) fires an LLM call after every turn.
- Periodic extraction (`@work` `exclusive=False`) can overlap the next turn's main call.
- Background summary regeneration (`@work` `exclusive=False`) runs every ~10 min.

The 2 s `asyncio.sleep` in `_extract_facts` and `generate_user_summary` is a band-aid. Real overlap → requests queue, time out, or error, and the errors are silently swallowed ("extraction failed").

**Recommendation:** Gate all non-primary LLM calls behind a single asyncio semaphore (or `@work(exclusive=True)`) so only one LLM request is in flight. Consider a dedicated extraction queue.

### 3.4 `aget_event_loop()` deprecation — LOW
See 3.2 — `asyncio.get_event_loop()` is deprecated when a loop is running; use `get_running_loop()`.

---

## 4. Performance & Scale

### 4.1 O(n) full-scan on every search — HIGH
Both backends read **every file / all documents** on every query:
- `FileMemoryStore._scoring_search` reads every `.md` in the subdir each call.
- `episodic_search`, `episodic_get_conversation`, `episodic_prune` scan all files too.
- `ChromaMemoryStore.episodic_search` falls back to `collection.get()` (fetch all) whenever a query returns nothing or when no query is given.

The docs admit "Fast for <1000 entries; degrades gracefully." Episodic records accumulate fast (one per turn + one per planner note + conflicts per extraction), so this crosses the threshold quickly. No index, no pagination, no filter-pushdown.

**Recommendation:** For the file backend, add a lightweight index (a small JSONL sidecar or a SQLite mirror) keyed by `event_type`/`conversation_id`/`category`. For Chroma, use `where` filters instead of fetch-all-then-filter.

### 4.2 Redundant / overlapping extraction burns LLM calls — HIGH
Extraction runs:
1. **Inline**, after every turn, in `acteract` ([`base.py:600`](src/gaia/core/agent/base.py:600)).
2. **Periodically**, every 3 turns, 5 s later ([`run_agent_execution`](src/gaia/gaia_tui_app.py:655-661)).
3. **At shutdown** ([`force_exit`](src/gaia/gaia_tui_app.py:435)) — see the race in 3.1.

Plus a hard `asyncio.sleep(2)` per extraction call. That's 2–3 LLM calls per user turn at a local backend. Notably, [`streaming.py`](src/gaia/streaming.py:109-111) comments say extraction is "batch-extracted once per session at shutdown" — which is **false** and contradicts `acteract`. Stale comment + three code paths.

**Recommendation:** Pick **one** strategy. Per-turn inline (immediate availability, 1 extra call/turn) **or** batch every N turns / at shutdown (fewer calls). Remove the others and fix the misleading comment.

### 4.3 No context-window management — HIGH
`message_history` is forwarded **unbounded** to the LLM every turn ([`acteract`](src/gaia/core/agent/base.py:556-557)), with no compression, eviction, or summarization. The spec describes this as Phase 4 ("context window management with automatic compression") but nothing implements it. Long conversations will overflow the context window, blow up latency/cost, and eventually fail.

**Recommendation:** Prioritize this as a core correctness issue (not "Phase 4 polish"): implement a sliding window + summarization of older turns into episodic memory, or an eviction policy that caps `message_history` tokens.

### 4.4 Weak ranking by recency — LOW
`FileMemoryStore._scoring_search` sorts `(score, timestamp)` descending, so a **weak match on a recent fact outranks a strong match on an old one**. With only keyword scoring, recency dominates.

**Recommendation:** Weight match quality above recency, or add TF-IDF / BM25-style scoring.

---

## 5. Architecture & Design Smells

### 5.1 Memory lifecycle split from construction — MEDIUM
The factory creates a store but **does not `initialize()`** (deferred to the event loop via `create_init_task` in `memory_lifecycle.py`). Meanwhile `Gaia.__init__` stores the not-yet-initialized instance and begins calling its methods. If init fails after construction, the agent holds a broken store. The split is fine in principle but the failure surface isn't coordinated.

**Recommendation:** Have the factory accept an already-initialized store, or make `Gaia` await store init before use.

### 5.2 Frozen `Settings` + `memory_dir` duplication — LOW/MEDIUM
`Settings.to_dict()` writes `memory_dir` at the **top level** and `memory.backend` inside `memory`, while `from_dict` reads `memory_dir` from the top level. The round-trip works only because `load_config` always merges over `DEFAULT_SETTINGS` first (the code even notes this "is safe in practice"). Fragile contract. Also, changing `memory.backend` at runtime does nothing — the store is created once in `__init__` and never rebuilt; the settings modal doesn't even expose backend selection.

**Recommendation:** Centralize the memory config in one place; document (or enforce) that backend changes require a restart.

### 5.3 Duplicated slash-command parsing — LOW
`config.parse_slash_command`, `is_slash_input`, `EXIT_COMMANDS`, and `is_exit_command` exist in [`config.py`](src/gaia/config.py) but are **not used** by the app — [`slash_commands.py`](src/gaia/slash_commands.py:73-75) re-parses `raw_command[1:].split(maxsplit=1)` inline. Dead/duplicated parsing surface.

**Recommendation:** Route everything through `config.parse_slash_command` / `is_exit_command`, or delete the unused helpers.

### 5.4 `_debug_mode` as a class attribute — NIT
`_debug_mode = False` is defined at class scope on `GaiaTUIApp`; assigning `self._debug_mode = True` shadows it with an instance attribute. Works, but it's a smell and resets every session (not persisted).

### 5.5 No cross-session conversation identity — LOW
`__conv_id` is a fresh UUID per `Gaia` instance, so a "conversation" only spans one app session. Continuity relies entirely on `user_summary.md` and episodic search. Acceptable for v1 but worth knowing before multi-session features (spec Phase 6) land.

---

## 6. Memory Lifecycle & Retention

### 6.1 `episodic_prune` never called — HIGH
`episodic_prune` is defined in both stores and the protocol, and tested, but **no application code calls it** (verified by grep). The spec's retention policy ("prune after N days, default 30") has no code behind it. Combined with unbounded episodic growth (6.2), memory expands forever.

**Recommendation:** Wire a scheduled `episodic_prune` (e.g. in the periodic-extraction loop or a retention `@work` task), configurable via settings.

### 6.2 Episodic records grow without bound — HIGH
Every turn writes a `conversation_turn` episode; the planner adds a `planner_note` episode when it notes anything; extraction adds `[CONFLICT]` facts. With no pruning (6.1) and no summarization of old episodes, the store grows linearly and forever. The file backend also re-reads everything per query (4.1), so this compounds.

### 6.3 Dedup via single near-match blocks all similar facts — LOW
`semantic_similarity_search` returns a boolean; `post_turn_extract` skips a fact if **any** of the top-5 hits is within Jaccard 0.75. After storing "I prefer Python," any future Python mention is deduped regardless of whether it's genuinely new. The conflict detector (0.3 < jaccard < 0.75) is a fragile heuristic that can misfire.

---

## 7. Documentation Drift

### 7.1 `ARCHITECTURE.md` no longer matches the code — HIGH
It omits `core/memory/factory.py`, `memory_lifecycle.py`, `slash_commands.py`, `streaming.py`, `fact_extraction.py`, and describes `src/app/`, in-app method bodies, and an old extraction model. The "Known gaps" section still claims the agent is "not wired to settings" and the endpoint default is the only inconsistency — but `reconfigure()` **is** called by slash commands (model + endpoint are wired), so that gap is already resolved in code but not in the doc.

**Recommendation:** Regenerate the architecture doc from the current code (or better, keep it generated). At minimum, fix the module layout, the extraction strategy description, and the "agent not wired" claim.

### 7.2 Spec vs code mismatches — MEDIUM
- Spec says memory backend is `"sqlite" | "redis" | "file"`; code supports `"file" | "chroma"`.
- Spec lists SQLCipher/FTS5 as implemented v1; neither exists in code.
- Spec's extraction (2 s delay, best-effort, `confidence=0.8`) partially matches but the code adds planner-driven retrieval and conflict detection not described.

**Recommendation:** Align spec/ARCHITECTURE.md with the actual implementation before the next release, or they'll mislead contributors (and this review).

---

## 8. Testing Gaps

### 8.1 New modules are largely untested — MEDIUM
`streaming.py`, `fact_extraction.py`, `memory_lifecycle.py`, `slash_commands.py`, and `factory.py` have no dedicated test files. Coverage is indirect at best via `test_app.py`. The pieces with the most logic risk (streaming animation/state, slash dispatch, factory) are the least directly tested.

### 8.2 Duplicate test files with unclear ownership — MEDIUM
There are **pairs**: `test_agent.py`/`test_agent_new.py`, `test_app.py`/`test_app_new.py`, `test_slash_commands.py`/`test_slash_commands_new.py`, plus `test_phase3.py`/`test_phase4.py`. The `test_phase*` and `_new` files don't clearly map to current modules — likely stale from earlier refactors. Run the suite and confirm what actually passes; dead tests give false confidence.

**Recommendation:** Consolidate to one authoritative file per area, delete the stale `*_new`/`*_phase*` files, and add focused tests for the streaming loop and slash dispatch.

---

## 9. Prioritized Recommendations (triage order)

| # | Issue | Severity | Effort | First action |
|---|---|---|---|---|
| 1 | Encryption advertised but unimplemented (§1.1) | CRITICAL | High | Decide: implement or remove the claim |
| 2 | CWD-relative settings/logs (§2.1) | HIGH | Low | Pin to `~/.gaia/` |
| 3 | Overlapping concurrent LLM calls (§3.3) | HIGH | Medium | Single LLM semaphore / exclusive queue |
| 4 | No context-window management (§4.3) | HIGH | High | Sliding window + summarize old turns |
| 5 | Redundant 2–3× per-turn extraction (§4.2) | HIGH | Low | Pick one extraction strategy; fix comment |
| 6 | `episodic_prune` never called (§6.1) | HIGH | Low | Wire a scheduled prune |
| 7 | O(n) full-scan searches (§4.1) | HIGH | Medium | Add an index / filter-pushdown |
| 8 | Final shutdown extraction race (§3.1) | HIGH | Low | Await or remove |
| 9 | Self-modifying system prompt (§1.2) | HIGH | Medium | Version + approval gate; stop treating prompt as mutable |
| 10 | Endpoint check false-ONLINE (§1.4) | MEDIUM | Low | Ping a real health endpoint |
| 11 | `ARCHITECTURE.md` stale (§7.1) | HIGH | Low | Regenerate from code |
| 12 | `save_config` non-atomic (§2.2) | MEDIUM | Low | Temp-file + `os.replace` |
| 13 | Broad `except: pass` masking (§2.3) | MEDIUM | Medium | Narrow handlers, surface store/agent mismatch |
| 14 | Memory content as instructions (§1.3) | MEDIUM | Low | Delimit retrieved memory as data |
| 15 | Duplicate/`*_new`/`*_phase` tests (§8.2) | MEDIUM | Low | Consolidate, delete stale |
| 16 | New modules untested (§8.1) | MEDIUM | Medium | Add tests for streaming + slash dispatch |
| 17 | Spec/code drift (§7.2) | MEDIUM | Low | Align spec |
| 18 | Frozen `Settings`/`memory_dir` dup (§5.2) | LOW | Low | Centralize memory config |
| 19 | Duplicated slash parsing (§5.3) | LOW | Low | Route through `config` |
| 20 | Weak recency-weighted ranking (§4.4) | LOW | Medium | BM25-style scoring |

---

*Generated from a read-only architecture review of the `refactor` branch. File paths/lines are relative to the repo root. Re-run after triage to see what's resolved.*
