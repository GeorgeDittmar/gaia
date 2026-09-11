# Spec: Memory Layer for the G.A.I.A. Core Agent

**Status:** Draft · **Date:** 2026-09-08 · **Owner:** (unassigned)
**Scope:** Add a two-tier memory system to the `Gaia` core agent
(`src/gaia/core/`) so G.A.I.A. behaves like a real personal assistant — one that
remembers the current conversation *and* retains durable facts across sessions.

---

## 1. Motivation

Today the core agent is **stateless**. In `src/gaia/core/agent/base.py`,
`Gaia.ainteract(prompt)` calls `self.__core_agent.run_stream(prompt)` with a single
fresh prompt every time — no history, no `system_prompt`, and the configured
`model`/`endpoint` are ignored (the endpoint is hard-coded to `http://localhost:8080/v1`).

Consequences:

- Multi-turn conversation doesn't actually work. The TUI *shows* a conversation in
  `main.py`, but each turn is sent to the model in isolation — G.A.I.A. forgets
  everything the moment you send the next message.
- Nothing persists between runs, so the "vault" concept already referenced in the UI
  ("SQLCipher Vault", the `encrypted` toggle in `settings.json`) has no backing.

This spec adds the memory layer to fix both, using the scaffolding that already
exists but is empty: `src/gaia/core/memory/{base,shortterm,longterm}.py`.

### Goals

- **G1** — Support genuine multi-turn conversation within a session.
- **G2** — Persist discrete, durable memories (facts / preferences) across sessions.
- **G3** — Recall relevant long-term memories and inject them into the model's context.
- **G4** — Keep everything local and private; support encryption of the durable store.
- **G5** — Fail gracefully: if memory is unavailable or disabled, the chat still works
  (it degrades to today's stateless behavior, never crashes).

### Non-goals (v1)

- Multi-user / multi-session isolation, cross-device sync, cloud storage.
- Vector / embedding store (a *later* enhancement to recall quality, not the base).
- Tool-using or agentic memory (memory that triggers actions).
- A full memory-management UI (beyond a few slash commands).

---

## 2. Concepts

| Term | Meaning |
|------|---------|
| **Short-term memory (STM)** | The rolling in-session conversation, held as pydantic-ai `ModelMessage`s in RAM. Bounded. Cleared on `/clear` or restart. |
| **Long-term memory (LTM)** | Durable, cross-session store of discrete **memories** (facts, preferences, people, projects, decisions) on local disk. This is the "vault". |
| **Memory** | One atomic LTM entry (a short natural-language statement + metadata). |
| **Recall** | Retrieving the top-K most relevant LTM memories for the current turn and injecting them into context. |
| **Extraction** | Deriving candidate LTM memories from a conversation (explicit via command, or automatic via an LLM pass). |
| **MemoryManager** | The component composed into `Gaia` that owns STM + LTM and orchestrates recall / extraction / history-building. |

Two tiers mirror the existing empty stubs (`shortterm.py`, `longterm.py`) and a
common `base.py` for the shared interface.

---

## 3. Architecture

```
                        ┌─────────────────────────────────────────────┐
   user turn            │                 MemoryManager               │
   ───────────────►     │                                             │
   (main.py)            │  ┌───────────┐        ┌──────────────────┐  │
        │               │  │  STM      │        │      LTM         │  │
        │               │  │ (RAM,     │  recall│ (disk: SQLite /  │  │
        │               │  │ bounded)  │◄───────│  SQLCipher vault)│  │
        │               │  │           │        │                  │  │
        │               │  │ build     │  extract│ (explicit or    │  │
        │               │  │ history   │────────►│  auto from      │  │
        │               │  └─────┬─────┘        │  conversation)   │  │
        │               │        │              └──────────────────┘  │
        ▼               │        │  message_history (ModelMessages)    │
   ┌─────────────┐      └────────┼────────────────────────────────────┘
   │    Gaia     │               │
   │  (agent)    │◄──────────────┘
   │  __core_    │  run_stream(prompt, message_history=...)
   │  agent      │  (pydantic-ai Agent)
   │  ─────────  │──────────────►  local LLM endpoint
   └─────┬───────┘   stream_text(delta)
         │  tokens
         ▼
      main.py (TUI renders streaming reply)
```

**Data flow per turn:**

1. `main.py` calls `Gaia.ainteract(prompt)`.
2. `Gaia` asks `MemoryManager.recall(prompt)` → top-K relevant LTM memories.
3. `MemoryManager.build_history(prompt, recalled)` assembles an ordered
   `message_history`: `[system prompt + recalled memories]` + STM turns + (the new
   prompt is passed separately to `run_stream`).
4. `Gaia` runs the pydantic-ai agent with that history and streams tokens back.
5. On completion, `Gaia` tells `MemoryManager.add_exchange(prompt, response)` →
   append to STM (and trim to the bound).
6. Optionally, `MemoryManager.maybe_extract(prompt, response)` → propose/persist new
   LTM memories (off by default; see §8).

**Component responsibilities**

- `MemoryManager` — facade; owns STM + LTM; `recall`, `build_history`,
  `add_exchange`, `maybe_extract`, `clear_short_term`.
- `ShortTermMemory` — append/trim an in-RAM list of `ModelMessage`s; enforce the bound.
- `LongTermMemory` — persistence + retrieval backend (add / query / forget / list).
- `base.py` — the shared abstract interface(s) so backends are swappable.

---

## 4. Short-term memory

**What it stores:** the session's conversation as pydantic-ai `ModelMessage`s
(requests and responses), in RAM.

**Lifecycle:** created when `Gaia` is constructed (per app launch); cleared by
`/clear` and on exit. Not persisted.

**Bounding strategy** — keep the context within a budget so it doesn't grow
unbounded or exceed the model's context window:

- **v1 default — sliding window by turns:** keep the most recent `N` exchanges
  (an exchange = one user turn + one model response). `N = memory.short_term.max_turns`,
  default **20**.
- **Refinement — token-aware trim:** when the accumulated history approaches a token
  budget, drop oldest turns first (still keeping the system/recall block). Use a rough
  char→token estimate (e.g. `len(text)/4`) for v1; a real tokenizer later.
- **Later — summarization:** collapse the oldest evicted turns into a running summary
  message to preserve continuity beyond the window (Phase 4, optional).

**Edge cases**
- Empty history → single-turn behavior (identical to today).
- A very long single message → still appended; the token-aware trim handles overflow
  by dropping *oldest* turns, never truncating the newest user input.
- Turn interrupted before completion (user force-quits) → do **not** persist a
  partial exchange (see §7, generator cleanup).

---

## 5. Long-term memory

**What it stores:** discrete, self-contained memories — short natural-language
statements like *"User prefers dark mode"* or *"Project X deadline is Oct 12"*.

**Storage backend — recommended: SQLite via stdlib `sqlite3`** (no new dependency),
with **FTS5** for keyword retrieval. This is structured, queryable, and single-file.
When `encrypted: true`, the same schema runs under **SQLCipher** (a later phase —
§8; requires a crypto backend such as `sqlcipher3`/`pysqlcipher3`).

*Alternatives considered:*
- **JSON file** — zero deps, matches the existing `settings.json` pattern, but no
  structured queries / ranking. Acceptable as a throwaway prototype, not recommended.
- **Vector store (Chroma/LanceDB)** — best recall quality, but heavy deps and overkill
  for v1. Keep as a Phase 4 recall upgrade layered *on top of* the SQLite store.

**Data model**

```sql
CREATE TABLE memories (
    id         TEXT PRIMARY KEY,          -- uuid4
    content    TEXT NOT NULL,             -- the memory, natural language
    category   TEXT NOT NULL DEFAULT 'fact',   -- preference|fact|person|project|decision|note
    salience   REAL NOT NULL DEFAULT 1.0, -- importance weight, used to rank recall
    source     TEXT NOT NULL DEFAULT 'explicit', -- explicit|extracted
    created_at TEXT NOT NULL,             -- ISO-8601 UTC
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, content=memories, content_rowid=rowid
);  -- full-text index over content, kept in sync via triggers
```

**Write path (extraction)**
- **Explicit** — `/remember <text>` and clear in-band cues ("remember that…"). Saved
  with `source='explicit'`, higher default `salience` (e.g. 2.0).
- **Automatic** (Phase 3, off by default) — after a turn, a lightweight LLM pass
  proposes 0–N candidate memories from the exchange; each is **deduped** against
  existing (FTS near-match / embedding similarity later) before persisting with
  `source='extracted'`, lower `salience`.
- **Forgetting** — `/forget <query>` deletes matches; optional periodic pruning of
  very low-salience, stale entries.

**Read path (recall)**
- Given the current prompt (+ a bit of recent context), retrieve top-K memories.
- **v1 ranking:** FTS5 relevance × `salience` × recency decay. `K =
  memory.long_term.recall.top_k`, default **5**.
- **Injection:** recalled memories are folded into the **system prompt** (or a
  clearly-delimited "Relevant memories:" block) so the model sees them every turn
  without polluting the conversation history.

---

## 6. Integration with `Gaia` and pydantic-ai

> The `message_history` API and part-constructor names below are **representative of
> pydantic-ai v2** — confirm exact signatures against the installed version
> (`ai.pydantic.dev/concepts/messages/`) when implementing.

**Current** (`src/gaia/core/agent/base.py`):

```python
class Gaia:
    def __init__(self, model_name: str = ""):
        model = OpenAIChatModel("local-model", provider=OpenAIProvider(
            base_url="http://localhost:8080/v1", api_key="not-needed"))
        self.__core_agent = Agent(model)

    async def ainteract(self, prompt: str):
        async with self.__core_agent.run_stream(prompt) as result:
            async for chunk in result.stream_text(delta=True):
                yield chunk
```

**Target** (sketch):

```python
from pydantic_ai.messages import (
    ModelRequest, ModelResponse,
    SystemPromptPart, UserPromptPart, ModelTextPart,
)

class Gaia:
    def __init__(self, settings: dict | None = None):
        settings = {**DEFAULT_SETTINGS, **(settings or {})}
        self.__settings = settings

        model = OpenAIChatModel("local-model", provider=OpenAIProvider(
            base_url=settings["endpoint"], api_key="not-needed"))
        # FIX existing gap: thread the configured system_prompt + endpoint through
        self.__core_agent = Agent(model, system_prompt=settings["system_prompt"])
        self.__memory = MemoryManager.from_settings(settings)

    async def ainteract(self, prompt: str):
        if not self.__memory.enabled:
            async for tok in self.__core_agent.run_stream(prompt):   # today's path
                yield tok
            return

        recalled   = await self.__memory.recall(prompt)              # LTM → context
        history    = self.__memory.build_history(recalled)          # SystemPrompt + STM turns
        full = ""
        async with self.__core_agent.run_stream(prompt, message_history=history) as result:
            async for chunk in result.stream_text(delta=True):
                full += chunk
                yield chunk
        self.__memory.add_exchange(prompt, full)                    # → STM (trim to bound)
        await self.__memory.maybe_extract(prompt, full)             # → LTM (opt-in)
```

`MemoryManager.build_history` returns, in order:

```python
[
    ModelRequest(parts=[
        SystemPromptPart(content=system_prompt + recalled_block),  # base system + memories
        *<oldest STM user turns as UserPromptPart / ModelResponse as ModelTextPart>...
    ]),
    # ... alternating ModelRequest / ModelResponse for each retained STM turn ...
]
# the live `prompt` is passed as run_stream's first arg, appended as the newest user turn
```

**What changes in `main.py`**
- Construct the agent with settings: `self.__core_agent = Gaia(settings=self.settings)`
  (currently `Gaia()` with no args — this also fixes the hard-coded endpoint/model).
- When `/settings` or `/model` changes config, rebuild / update the `Gaia` instance so
  the new endpoint/model/system_prompt take effect (today `/model` is display-only).
- `/clear` → call `self.__core_agent.clear_memory()` (clears STM, leaves LTM intact).

---

## 7. Public API (interface sketch)

```python
# src/gaia/core/memory/base.py
class Memory(ABC):
    enabled: bool

# src/gaia/core/memory/shortterm.py
class ShortTermMemory:
    def __init__(self, max_turns: int = 20) -> None: ...
    def append(self, user_text: str, assistant_text: str) -> None: ...
    def messages(self) -> list[ModelMessage]: ...      # oldest → newest, within bound
    def clear(self) -> None: ...
    def __len__(self) -> int: ...                       # retained turn count

# src/gaia/core/memory/longterm.py
class LongTermMemory:
    def __init__(self, path: str | Path, *, encrypted: bool = False) -> None: ...
    async def add(self, content: str, *, category: str = "fact",
                  salience: float = 1.0, source: str = "explicit") -> str: ...
    async def recall(self, query: str, *, top_k: int = 5) -> list[MemoryRecord]: ...
    async def forget(self, query: str) -> int: ...      # returns # deleted
    async def list(self, *, limit: int = 50) -> list[MemoryRecord]: ...
    def close(self) -> None: ...

@dataclass
class MemoryRecord:
    id: str
    content: str
    category: str
    salience: float
    source: str
    created_at: str

# src/gaia/core/memory/__init__.py  (facade)
class MemoryManager:
    @classmethod
    def from_settings(cls, settings: dict) -> "MemoryManager": ...
    enabled: bool
    def build_history(self, recalled: list[MemoryRecord]) -> list[ModelMessage]: ...
    def add_exchange(self, user_text: str, assistant_text: str) -> None: ...
    def clear_short_term(self) -> None: ...
    async def recall(self, prompt: str) -> list[MemoryRecord]: ...
    async def maybe_extract(self, prompt: str, response: str) -> None: ...  # opt-in
    async def remember(self, text: str) -> str: ...                          # explicit
    async def forget(self, query: str) -> int: ...
    async def list_memories(self, *, limit: int = 50) -> list[MemoryRecord]: ...
```

---

## 8. UX — slash commands

| Command | Behavior |
|---------|----------|
| `/remember <text>` | Save an explicit memory (`source='explicit'`). |
| `/forget <query>` | Delete matching memories; `/forget all` wipes the vault. |
| `/memories` | List the most recent/relevant LTM memories. |
| `/memory on\|off` | Toggle memory usage (STM + recall) at runtime. |
| `/clear` | **Clears short-term only** (the conversation). Does **not** touch the vault. |
| `/status` | Add a line: `Memory: <N> short-term turns · <M> long-term facts`. |

Each new command must be added to **both** `SLASH_COMMANDS` (autocomplete + help) and
the `match` dispatch in `handle_slash_command` (see CLAUDE.md conventions).

---

## 9. Configuration

New `memory` block in `settings.json` (defaults shown):

```json
"memory": {
  "enabled": true,
  "short_term": { "max_turns": 20, "strategy": "sliding_window" },
  "long_term": {
    "store_path": "~/.gaia/memory.db",
    "encrypted": true,
    "recall": { "enabled": true, "top_k": 5 },
    "auto_extract": false
  }
}
```

Notes:
- `store_path` defaults to a **user dir** (`~/.gaia/`), not the repo — keeps the
  vault out of version control and per-machine.
- `long_term.encrypted` should default to the top-level `encrypted` flag if unset, so
  the existing toggle stays the single source of truth for "is the vault encrypted".
- `auto_extract` defaults to **off** (privacy + local-LLM cost); user can enable.

---

## 10. Privacy & data

- **Local-only.** The store and all memory processing happen on the user's machine;
  nothing is sent anywhere except the (local) LLM endpoint.
- **Encryption.** When `encrypted: true`, the durable store is encrypted at rest
  (SQLCipher). Until that phase lands, the flag is honored for *intent* but the store
  is plaintext — state this honestly in `/status` (don't claim AES that isn't there,
  mirroring the gap already flagged in CLAUDE.md).
- **Wipe.** `/forget all` (and deleting the store file) fully removes memories.
- **No telemetry.** Consistent with the project's stated identity.

---

## 11. Failure modes & degradation

| Situation | Behavior |
|-----------|----------|
| Store file missing | Create it; start empty. |
| Store unreadable/corrupt | Log a warning, **fall back to STM-only** (or memory-off). Chat never crashes. |
| Memory disabled (`enabled: false` or `/memory off`) | `ainteract` uses the plain single-prompt path (today's behavior). |
| Recall returns nothing | Proceed with system prompt + STM only. |
| Auto-extract errors | Skip extraction; the exchange is still stored in STM. |
| Model endpoint down | Memory ops are local and unaffected; the stream fails exactly as today. |
| SQLite concurrency | Use **WAL** mode; single-writer access (the app is single-user). |
| Turn force-quit mid-stream | Don't persist a partial exchange (guard with the generator's cleanup /
  `is_shutting_down` check already used in `main.py`). |

---

## 12. Testing plan

- **Unit — STM:** append/trim respects `max_turns`; ordering oldest→newest; `clear()`
  empties it; property: retained turns never exceed the bound.
- **Unit — LTM:** `add`/`list`/`forget` round-trip; `recall` ranks by salience + FTS;
  dedup (Phase 3) doesn't insert near-duplicates.
- **Unit — build_history:** output order is `[system(+recalled)]` then STM turns;
  recalled memories appear in the system block; empty STM → just the system block.
- **Integration — multi-turn (proves G1):** 3-turn conversation where turn 3 references
  something from turn 1; assert the model's context included turn 1 (i.e. STM works).
- **Integration — persistence (proves G2/G3):** `/remember X` → construct a **new**
  `Gaia` (simulating a restart) → recall a prompt related to X → X is retrieved.
- **Degradation:** with `memory.enabled = false`, a turn's behavior matches today's
  stateless output (no history sent).
- **Resilience:** corrupt store → app starts, chat works, warning logged.

Run with the project's (still-to-be-added) test runner; none exists yet — see CLAUDE.md.

---

## 13. Delivery phases

| Phase | Deliverable | Exit criterion |
|-------|-------------|----------------|
| **1 — Short-term** | STM + `build_history` + `message_history` wired into `ainteract`; thread `settings` (endpoint/model/system_prompt) into `Gaia`; `/clear` resets STM. | G.A.I.A. genuinely remembers the conversation; `/clear` forgets it. |
| **2 — Long-term (explicit)** | SQLite store + FTS5; `MemoryManager`; `/remember` `/forget` `/memories`; recall injected into system prompt. | A memory survives an app restart and is recalled into a later turn. |
| **3 — Auto-extraction + ranking** | Opt-in LLM extraction with dedup; salience + recency ranking. | Relevant extracted memories surface without the user running `/remember`. |
| **4 — Privacy + recall quality** | SQLCipher encryption tied to `encrypted`; export/wipe; optional embeddings/vector recall; optional summarization for the STM window. | Encrypted vault verified; recall improves on keyword baseline. |

Phase 1 is deliberately the smallest high-value step: it turns the fake
conversation into a real one and fixes the settings gap in the same pass.

---

## 14. Open questions

1. **Store location** — `~/.gaia/` (recommended) vs. a path the user sets? Confirm.
2. **`/clear` semantics** — clear STM only (proposed). Should there be a separate
   `/reset all` that also wipes LTM?
3. **Auto-extract default** — off (proposed, for privacy + local cost). Agree?
4. **Encryption dependency** — is a SQLCipher binding acceptable, or should v1 stay
   plaintext and treat `encrypted` as a documented TODO?
5. **Single-user assumption** — fine for v1; defer multi-session until needed?
6. **`Gaia` re-creation on `/model` change** — rebuild the agent vs. hot-swap the model?

---

## 15. Out of scope (deferred)

- Multi-user / multi-session isolation and per-user vaults.
- Cross-device sync or any network persistence.
- Vector database as the *primary* store (may join as a recall index in Phase 4).
- Agentic / tool-using memory (memories that drive actions or schedule reminders).
- Rich memory-management UI (graph view, per-memory provenance, editing).
