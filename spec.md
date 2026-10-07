# G.A.I.A. — Feature Specification

*General AI Assistant — Privacy-First, Local, Modular Agent*

---

## 1. Vision

G.A.I.A. is a **general-purpose, privacy-first AI assistant** that runs entirely on the user's own hardware. No telemetry, no cloud calls, no data exfiltration. It is a modular agent framework — not a chatbot — with long-term memory, tool use, and extensible integrations via MCP (Model Context Protocol).

### Core Principles

1. **Privacy by default.** Everything that leaves the machine is opt-in, explicit, and reversible. The default is air-gapped.
2. **Local-first.** All inference, memory, and tool execution happen locally unless the user explicitly configures an external endpoint.
3. **Modular.** Every subsystem is a pluggable module. Swap the LLM backend, memory store, or tool provider without touching the rest.
4. **Transparent.** The user can inspect, edit, and audit every decision the agent makes — memory contents, tool calls, reasoning traces.
5. **General purpose.** Not domain-restricted. The agent adapts to the user's workflow, learns from interaction, and accumulates capability over time.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   GaiaTUI App                       │
│  (Textual TUI — chat, settings, modals, status)     │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                 Agent Orchestrator                  │
│  - Task decomposition & planning                    │
│  - Tool / MCP dispatcher                            │
│  - Memory read/write coordinator                    │
│  - Session / conversation management                │
└──┬──────────┬───────────────┬───────────┬───────────┘
   │          │               │           │
   ▼          ▼               ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────────┐
│  MCP   │ │ Memory │ │ Tools/   │ │  Skills/     │
│ Client │ │ Store  │ │ Services │ │  Procedures  │
│        │ │        │ │ (filesystem│ │             │
│ (MCP   │ │        │ │  clipboard│ │ (prompt     │
│  SDK)  │ │        │ │  email,  │ │  templates, │
│        │ │        │ │  browser │ │  policies)  │
└────────┘ └────────┘ └──────────┘ └──────────────┘
```

The agent orchestrator is the central dispatch layer. It receives user input, decides whether to answer directly, invoke a tool, query memory, or compose a multi-step plan. Memory reads and writes are side-car operations — the agent doesn't "know" about memory internally; the orchestrator injects relevant memories into the context window at runtime.

---

## 3. Memory System

Memory is the agent's persistent identity. Three orthogonal types, each with its own storage backend and retrieval strategy. All memory is stored locally (SQLite + SQLCipher when encryption is enabled) with optional end-to-end encrypted cloud sync for multi-device setups.

### 3.1 Semantic Memory — *What the user knows*

Facts and concepts independent of time or context. The agent's "encyclopedia."

**Contents:**
- User preferences (names, roles, communication style, domain expertise)
- Domain knowledge (project specs, coding conventions, APIs the user works with)
- Distilled summaries (learned from conversations — e.g., "the user prefers concise answers")
- Ontology / knowledge graph (entities, relationships, categories)

**Storage:** SQLite (v1) with FTS5 full-text search + optional vector embeddings.
The storage layer is abstracted behind a `MemoryStore` protocol — swapping to Redis, a document store, or a hybrid later only requires a new backend implementation, not a schema rewrite.

**Schema (SQLite):**

```sql
CREATE TABLE semantic_facts (
    fact_id     TEXT PRIMARY KEY,
    content     TEXT NOT NULL,          -- the fact or concept
    category    TEXT NOT NULL DEFAULT 'general',
    confidence  REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0 AND confidence <= 1),
    source      TEXT NOT NULL DEFAULT 'user',  -- 'user', 'agent', 'conversation', 'distillation'
    source_id   TEXT,                   -- reference to the conversation/episode that generated this
    created_at  TEXT NOT NULL,          -- ISO-8601 timestamp
    updated_at  TEXT NOT NULL,          -- ISO-8601 timestamp
    metadata    TEXT,                   -- JSON blob for extra tags, properties, etc.

    -- FTS5 for keyword search
    CONTENT TABLE semantic_facts_fts
);

CREATE VIRTUAL TABLE semantic_facts_fts USING fts5(
    content,
    category,
    content=semantic_facts,
    content_rowid=fact_id
);

-- Optional vector embeddings (stored as BLOB, backend-dependent format)
-- Could use a separate table or a column in semantic_facts
-- e.g., cosine similarity search against this vector
-- CREATE TABLE semantic_embeddings (fact_id TEXT PRIMARY KEY, vector BLOB);

-- Index for time-bounded queries
CREATE INDEX idx_semantic_updated ON semantic_facts(updated_at DESC);
```

**Operations:**
- `insert(content, category, confidence, source, source_id)` — add a new fact
- `update(fact_id, new_content, new_confidence)` — revise a fact
- `delete(fact_id)` — remove a fact
- `search(query, limit=10)` — semantic + keyword search (FTS5 now, vectors later)
- `query_graph(entities, relation)` — knowledge graph traversal (via `metadata` JSON)
- `list(category, limit)` — list facts by category
- `prune(older_than, retention_days)` — remove facts older than N days

**Redis-compatible mapping:**
- `semantic_facts` → hash keys: `gaia:semantic:<fact_id>` → `{content, category, confidence, source, source_id, created_at, updated_at, metadata}`
- FTS5 → RediSearch with `TAG` and `TEXT` fields (or swap to an embedded vector index)
- Vector search → Redis vector indexing (`HNSW` index on the vector field)

**Autodistillation triggers:**
- Post-turn extraction: after each conversation turn, a background task calls the LLM to extract facts from the user prompt + agent response pair. Runs on a 2-second delay after the turn completes (avoids competing with the streaming call). Best-effort — never blocks the response. Facts are tagged with `source="auto_extract"` and `confidence=0.8`.
- `/extract` slash command: manually re-processes the last N (configurable, default 10) turns from episodic memory and extracts any missed facts. Shows progress in chat ("Extracting facts..." → "X facts extracted"). Useful for backfilling when the feature is first enabled.
- After a conversation ends, scan for repeatable facts worth storing
- Periodic summarization of raw episodic traces into semantic summaries
- User correction: when the user says "that's wrong" or "actually, X is the rule," update semantic memory

**Auto-classification:** Extracted facts are classified into categories by keyword matching:
- `preference`: name, location, lives, job, role, prefers, likes, city, state, etc.
- `project`: project, repo, code, bug, feature, working on, building, github, docker, etc.
- `general`: everything else

**Privacy:** Semantic memory is the user's personal knowledge base. Never sent to any external service by default. If cloud sync is enabled, it is end-to-end encrypted with the user's key.

### 3.2 Episodic Memory — *What happened*

Time-indexed records of specific events and experiences. The agent's "autobiography."

**Contents:**
- Conversations (full transcript with turn boundaries, timestamps)
- Tool calls (what tool, what args, what result, latency)
- Context windows (what memory was retrieved for which interaction)
- Errors and corrections (what failed, how it was fixed)
- User feedback (explicit praise/criticism, implicit signals like re-prompting)

**Storage:** SQLite (v1). Episodes are partitioned by type and compressed into semantic summaries via autodistillation. The storage layer is abstracted — swapping backends only requires a new implementation.

**Schema (SQLite):**

```sql
CREATE TABLE episodic_events (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL CHECK(event_type IN (
        'conversation_start', 'conversation_turn', 'conversation_end',
        'tool_call', 'tool_result', 'error', 'correction',
        'memory_insert', 'memory_update', 'memory_delete',
        'config_change', 'mcp_connect', 'mcp_disconnect'
    )),
    conversation_id TEXT,         -- groups related events (e.g. all turns in one chat)
    timestamp   TEXT NOT NULL,    -- ISO-8601 timestamp
    summary     TEXT,             -- one-line summary for quick browsing
    payload     TEXT NOT NULL,    -- JSON blob with full event data
    created_at  TEXT NOT NULL,    -- ISO-8601 timestamp (for retention pruning)
    indexed_content TEXT,         -- flattened text for FTS search (summary + payload extraction)

    -- FTS5 for content search
    CONTENT TABLE episodic_events_fts
);

CREATE VIRTUAL TABLE episodic_events_fts USING fts5(
    indexed_content,
    event_type,
    content=episodic_events,
    content_rowid=event_id
);

CREATE INDEX idx_episodic_conversation ON episodic_events(conversation_id);
CREATE INDEX idx_episodic_timestamp ON episodic_events(timestamp DESC);
CREATE INDEX idx_episodic_created ON episodic_events(created_at);
```

**Operations:**
- `record(event_type, conversation_id, summary, payload)` — log an episode
- `search(time_range, event_types, query, limit)` — time-bounded search with optional text filter
- `get_conversation(conversation_id)` — retrieve all events for a conversation, ordered by timestamp
- `group_by_conversation()` — return distinct conversation groups (for browsing)
- `prune(before_timestamp)` — purge old episodes (configurable retention policy, default 30 days)
- `compress(old_episodes)` — summarize old episodes into semantic facts, then delete them

**Redis-compatible mapping:**
- `episodic_events` → hash keys: `gaia:episode:<event_id>` → `{event_type, conversation_id, timestamp, summary, payload, created_at}`
- FTS5 → RediSearch TEXT fields on `indexed_content`
- Conversation grouping → RediSearch TAG query on `conversation_id`
- Retention pruning → Redis `EXPIRE` on keys or TTL-based eviction

**Retention policy:**
- Recent episodes (last N days, configurable, default 30) stored in full detail
- Older episodes are summarized into semantic facts and the raw episode is pruned
- User can manually review and curate episodic memory at any time

**Privacy:** Episodes contain the most sensitive data (full conversations, tool outputs). They are encrypted at rest. The user controls retention and deletion. No episode data leaves the machine unless explicitly shared.

### 3.3 Procedural Memory — *How to do things*

Skills, routines, and policies for performing tasks. The agent's "muscle memory."

**Contents:**
- Prompt templates (standardized ways to ask the LLM for specific tasks)
- Multi-step routines (e.g., "how to debug a Python app" → step 1: read error, step 2: search semantic memory for project context, step 3: propose fix)
- Policies (rules about when to use which tool, when to ask the user for confirmation, safety guardrails)
- Agent code (the actual implementation of skills — Python functions registered as tools)
- MCP tool definitions (discovered from MCP servers at runtime)

**Storage:** YAML/JSON files on disk (v1, human-editable) + optional SQLite index for searchability. Procedural definitions are loaded at startup and hot-reloaded on change. The storage abstraction allows swapping to Redis or a config server later.

**Schema (SQLite index — optional, supplements on-disk YAML/JSON):**

```sql
CREATE TABLE procedure_entries (
    procedure_id    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'custom',
    safety_level    TEXT NOT NULL CHECK(safety_level IN ('safe', 'caution', 'danger')),
    prompt_template TEXT,             -- Jinja2/Python format string for LLM prompting
    steps           TEXT,             -- JSON array of ordered steps
    source_file     TEXT,             -- path to the on-disk YAML/JSON definition
    enabled         INTEGER NOT NULL DEFAULT 1,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,    -- ISO-8601 timestamp
    updated_at      TEXT NOT NULL,    -- ISO-8601 timestamp
    metadata        TEXT              -- JSON blob for tags, dependencies, etc.

    -- FTS5 for searching by description
    CONTENT TABLE procedure_entries_fts
);

CREATE VIRTUAL TABLE procedure_entries_fts USING fts5(
    name,
    description,
    category,
    content=procedure_entries,
    content_rowid=procedure_id
);

CREATE INDEX idx_proc_category ON procedure_entries(category);
CREATE INDEX idx_proc_enabled ON procedure_entries(enabled);
```

**Operations:**
- `register(procedure_id, name, description, prompt_template, steps, safety_level, source_file)` — add a skill
- `get(procedure_id)` — retrieve a procedure's full definition (from disk or index)
- `search(query, category)` — find procedures by natural language description
- `execute(procedure_id, context, agent)` — run a procedure with the given context
- `update(procedure_id, new_definition)` — hot-reload a skill without restart
- `list(category, enabled_only=True)` — list procedures by category
- `import_from_yaml(path)` — bulk-import procedures from a YAML file
- `export_to_yaml(path)` — dump all procedures to a YAML file

**Redis-compatible mapping:**
- `procedure_entries` → hash keys: `gaia:proc:<procedure_id>` → `{name, description, category, safety_level, steps, enabled, version, metadata}`
- FTS5 → RediSearch TEXT fields on `name`, `description`, `category`
- On-disk YAML/JSON files → kept as the source of truth, Redis as an in-memory cache with `EXPIRE` or manual invalidation

**Categories:**
- `communication` — writing emails, drafting messages, summarizing threads
- `coding` — debugging, refactoring, testing, documentation
- `research` — web search, file analysis, comparative summaries
- `productivity` — scheduling, task management, note organization
- `system` — file operations, process management, environment setup
- `custom` — user-defined skills

**Autodistillation triggers:**
- When the user repeats a multi-step task, suggest encoding it as a procedure
- After a successful tool chain, ask: "Should I remember this workflow?"
- Failed attempts can generate "anti-procedures" — things to avoid

**Privacy:** Procedures are user-defined logic. They can execute arbitrary local commands, so they are the highest-trust layer. The user must explicitly approve any procedure that performs destructive or network operations.

### 3.4 Storage Abstraction

All three memory types are accessed through a `MemoryStore` protocol (abstract base class). The protocol defines the interface; the implementation is chosen at runtime.

```python
class MemoryStore(Protocol):
    """Interface for all memory storage backends."""

    # Semantic
    async def semantic_insert(self, content: str, category: str,
                              confidence: float, source: str,
                              source_id: str | None) -> str: ...
    async def semantic_update(self, fact_id: str, new_content: str,
                              new_confidence: float) -> None: ...
    async def semantic_delete(self, fact_id: str) -> None: ...
    async def semantic_search(self, query: str, limit: int = 10
                              ) -> list[dict]: ...

    # Episodic
    async def episodic_record(self, event_type: str, conversation_id: str | None,
                              summary: str, payload: dict) -> str: ...
    async def episodic_search(self, time_range: tuple[str, str] | None,
                              event_types: list[str] | None,
                              query: str | None, limit: int = 20
                              ) -> list[dict]: ...
    async def episodic_get_conversation(self, conversation_id: str
                                        ) -> list[dict]: ...
    async def episodic_prune(self, before_timestamp: str) -> int: ...

    # Procedural
    async def procedure_register(self, procedure_id: str,
                                  name: str, description: str,
                                  prompt_template: str | None,
                                  steps: list[dict] | None,
                                  safety_level: str,
                                  source_file: str | None) -> None: ...
    async def procedure_get(self, procedure_id: str) -> dict | None: ...
    async def procedure_search(self, query: str, category: str | None
                               ) -> list[dict]: ...

    # Audit
    async def audit_log(self, action: str, actor: str, target_type: str | None,
                        target_id: str | None, detail: dict | None) -> None: ...

    # Lifecycle
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
```

**Built-in implementations:**
- `SQLiteMemoryStore` — v1 default. Uses SQLite + SQLCipher (if encryption enabled). Supports FTS5 search.
- `ChromaMemoryStore` — vector-backed store using ChromaDB + `all-MiniLM-L6-v2` ONNX embeddings. Handles stopwords, apostrophes, and punctuation better than FTS5 for semantic queries (e.g., "what is my name?" returns results where FTS5 finds nothing). Persists to `~/.gaia/chroma/` by default.
- `RedisMemoryStore` — planned v2+. Uses Redis with RediSearch for full-text and vector search. Leverages `EXPIRE` for retention, `HGETALL/HSET` for individual records.
- `FileStore` — lightweight, no dependencies. Reads/writes JSON/YAML files directly. Useful for development or single-user single-device setups where SQLCipher is overkill.

**Choosing a backend:**
The backend is configured in `settings.json` under `memory.backend` (`"sqlite"`, `"redis"`, `"file"`). The orchestrator creates the appropriate store at startup. The `MemoryStore` protocol ensures the agent and UI don't care which backend is used.

---

## 4. MCP Integration

G.A.I.A. integrates with the **Model Context Protocol (MCP)** as its primary tool discovery and execution mechanism.

### 4.1 MCP Client

- Uses the official MCP SDK to connect to MCP servers (stdio, SSE, or HTTP transport)
- Servers are configured in `settings.json` under `mcp_servers`
- Supports stdio servers (local executables) and HTTP/SSE servers (networked)
- Server connections are lazy — only started when a tool from that server is needed
- Connection failures are non-fatal — the agent reports unavailable tools to the user

### 4.2 Tool Discovery & Registration

On startup (or when MCP config changes):
1. Connect to each configured MCP server
2. Discover available tools, resources, and prompts
3. Register them in the agent's tool catalog with metadata:
   - Name, description, parameter schema
   - Source server, category, safety level
   - Whether it requires user confirmation before execution
4. Make the catalog available to the agent orchestrator

### 4.3 Safety

- Each MCP tool has an optional safety level: `safe`, `caution`, `danger`
- `safe` tools execute without confirmation (read-only,信息查询)
- `caution` tools prompt the user ("Are you sure you want to send this email?")
- `danger` tools require explicit user approval + are logged to episodic memory
- User can override safety levels per-tool in settings
- All tool calls are logged to episodic memory with args and results

---

## 5. Agent Orchestrator

The orchestrator is the brain — it decides what to do and when.

### 5.1 Decision Pipeline

```
User input
    │
    ▼
┌─────────────┐     yes    ┌──────────┐
│ Slash cmd?  │───────────►│ Dispatch │
│ (F1, /exit│            │  command │
│  /model,   │            └──────────┘
│  etc.)     │
└──────┬─────┘
       │ no
       ▼
┌─────────────┐     yes    ┌──────────────┐
│ Short answer│───────────►│ Generate     │
│ possible?   │            │ direct reply │
└──────┬─────┘            └──────────────┘
       │ no
       ▼
┌─────────────┐     yes    ┌──────────────┐
│ Need memory │───────────►│ Inject       │
│ retrieval?  │            │ memories     │
└──────┬─────┘            └──────────────┘
       │ no
       ▼
┌─────────────┐     yes    ┌──────────────┐
│ Need tool/  │───────────►│ Plan +       │
│ MCP call?   │            │ execute      │
└──────┬─────┘            └──────────────┘
       │ no
       ▼
┌─────────────┐
│ Generate    │
│ reply       │
└─────────────┘
```

### 5.2 Planning

For complex requests, the orchestrator decomposes the task:
1. Analyze the request for subtasks
2. Build a plan (ordered list of steps)
3. Execute steps sequentially or in parallel where possible
4. Aggregate results into a coherent response
5. Update episodic memory with the plan and its outcome

Planning is optional and triggered when the request is non-trivial (multi-step, multi-domain, or requires tool chains). The agent can show the plan to the user before executing.

### 5.3 Context Management

- Each conversation has a context window with configurable size
- The context includes: conversation history, retrieved memory, active tools, current procedure
- When context approaches the limit, older messages are compressed (summarized) and pushed to episodic memory
- Semantic memories are retrieved at runtime based on query similarity — they don't count against the context window (they're injected selectively)

---

## 6. Tool Ecosystem

Beyond MCP, G.A.I.A. includes built-in tools.

### 6.1 Built-in Tools

| Category | Tools |
|---|---|
| **Filesystem** | Read, write, search, diff, glob, file metadata |
| **Process** | Run shell commands (with safety gating), list processes, environment info |
| **Network** | HTTP requests (GET/POST), DNS lookup |
| **Clipboard** | Read/write clipboard |
| **System** | OS info, disk usage, memory usage |
| **Coding** | Syntax validation, linting (where available), code formatting |
| **Communication** | Draft emails, format messages (sandboxed — sending requires user action) |

### 6.2 Tool Safety Gating

All tools have a safety level:
- `safe` — read-only, no side effects (file read, search, system info)
- `caution` — modifies state but is reversible (file write, process start, clipboard)
- `danger` — destructive or irreversible (file delete, process kill, network send)

Users configure the default safety level in settings. Below that level, tools require confirmation.

---

## 7. Privacy & Security Model

### 7.1 Data Classification

| Level | Data | Default Behavior |
|---|---|---|
| **L0** | UI state, settings | Never leaves machine |
| **L1** | Semantic memory | Never leaves machine |
| **L2** | Episodic memory | Encrypted at rest; optional encrypted cloud sync |
| **L3** | Tool outputs (may contain sensitive data) | Encrypted at rest; never cached externally |
| **L4** | LLM API calls | Only sent to user-configured endpoint |

### 7.2 Encryption

- **At rest:** SQLite database encrypted with SQLCipher (AES-256). Key is derived from user password or OS keychain.
- **In transit:** All outbound connections use TLS. The user controls which endpoints are allowed.
- **E2E sync (future):** If multi-device sync is enabled, data is encrypted client-side before upload. The server never sees plaintext.

### 7.3 Telemetry

**Zero telemetry by design.** No analytics, no crash reports, no usage metrics. If the user wants to enable diagnostics, it's an explicit opt-in feature.

### 7.4 Audit Log

All non-trivial actions are logged to an **append-only, encrypted-at-rest** audit trail.
The audit log records *what* happened, not *what was said* — it logs metadata (event type, timestamp, tool names, outcomes, durations), not raw content (no conversation bodies, no file contents, no tool output payloads). Full conversation transcripts belong in episodic memory instead.

**Logged events:**
- Memory modifications (what changed, when, why)
- Tool executions (especially `danger` level)
- MCP server connections and disconnections
- Configuration changes
- Memory retrieval operations (queries issued, results returned — no content)

**Properties:**
- **Append-only.** No entry can be deleted or modified — prevents silent scrubbing by the agent or a compromised process.
- **Encrypted at rest.** The audit log shares the same SQLCipher encryption as the rest of the database. It is never written in plaintext.
- **Retention-managed.** Subject to the same configurable retention policy as episodic memory (default: pruned after N days, configurable).
- **User-accessible.** Viewed via `/audit` in the TUI. Exportable as a signed JSONL file for external forensic review.

The audit log is stored in its own encrypted SQLite table (separate from episodic memory) for integrity and performance.

**Schema (SQLite):**

```sql
CREATE TABLE audit_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,        -- ISO-8601 timestamp
    action      TEXT NOT NULL,        -- 'tool_execute', 'memory_insert', 'memory_update',
                                      -- 'memory_delete', 'config_change', 'mcp_connect',
                                      -- 'mcp_disconnect', 'procedure_run'
    actor       TEXT NOT NULL DEFAULT 'agent',  -- 'agent', 'user', 'system'
    target_type TEXT,                 -- 'file', 'process', 'semantic_facts', 'episodic_events', etc.
    target_id   TEXT,                 -- identifier of the target resource
    detail      TEXT,                 -- JSON: tool_name, result, duration, status
    ip_address  TEXT,                 -- if applicable (remote actions)
    user_agent  TEXT                  -- if applicable
);

CREATE INDEX idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_target ON audit_log(target_type, target_id);
```

---

## 8. User Interface

### 8.1 Textual TUI (Current)

The existing Textual-based dark cyberpunk UI remains the primary interface, enhanced with:

- **Memory browser** — `/memory` opens a panel to browse/edit semantic and episodic memory
- **Tool inspector** — `/tools` lists available tools (MCP + built-in) with safety levels and test buttons
- **Procedure editor** — `/edit-procedure` opens a procedural memory editor
- **Plan viewer** — During complex tasks, shows the current plan with step-by-step progress
- **Audit viewer** — `/audit` shows the action audit log
- **Memory dashboard** — `status` command shows memory statistics (semantic count, episodic size, procedures loaded)

### 8.2 CLI Interface

```bash
gaia              # Launch TUI
gaia chat         # CLI-only chat (no TUI)
gaia memory       # Interactive memory browser
gaia tools        # List and test tools
gaia procedures   # Manage procedural memory
gaia settings     # Edit configuration
gaia import       # Import data from other AI assistants
gaia export       # Export all data (memory, logs, settings)
```

---

## 9. Configuration

### 9.1 settings.json

```json
{
  "model": "qwen2.5-coder:32b",
  "endpoint": "http://localhost:11434",
  "encrypted": true,
  "system_prompt": "...",

  "memory": {
    "semantic_enabled": true,
    "episodic_enabled": true,
    "procedural_enabled": true,
    "episodic_retention_days": 30,
    "max_context_tokens": 128000,
    "summarize_above_tokens": 64000
  },

  "mcp_servers": [
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/user/projects"]
    }
  ],

  "tools": {
    "default_safety_level": "caution",
    "require_confirmation_above": "caution",
    "danger_requires_approval": true
  },

  "sync": {
    "enabled": false,
    "encrypted": true,
    "storage_path": "~/.gaia-sync"
  }
}
```

---

## 10. Implementation Plan

### Phase 1: Foundation (current state)
- [x] Textual TUI skeleton
- [x] Basic agent harness (pydantic-ai integration)
- [x] Settings/config system
- [x] Endpoint polling
- [x] Slash commands (/model, /settings, /help, /clear, /status, /exit)
- [x] Basic autocomplete

### Phase 2: Memory Core
- [x] SQLite database schema (semantic, episodic, procedural tables)
- [x] SQLCipher encryption integration
- [x] Semantic memory: CRUD + FTS5 search
- [x] Episodic memory: recording + time-bounded search
- [x] Procedural memory: YAML-based registry + execution
- [x] ChromaDB vector-backed MemoryStore (`ChromaMemoryStore`) — pluggable backend selectable via `memory.backend` in settings
- [x] Post-turn fact extraction — background LLM call after each conversation turn, auto-classifies facts, saves to semantic memory with confidence 0.8
- [x] `/extract` slash command — backfills facts from recent episodic memory
- [ ] Autodistillation pipeline (episodic → semantic summarization)
- [ ] `/memory` TUI panel for browsing/editing

### Phase 3: MCP Integration
- [ ] MCP client (stdio + HTTP transport)
- [ ] Tool discovery & registration
- [ ] Tool catalog in TUI (`/tools`)
- [ ] Safety gating for MCP tools
- [ ] Tool execution in agent orchestrator
- [ ] Pre-configured MCP server templates (filesystem, github, etc.)

### Phase 4: Agent Orchestrator
- [ ] Decision pipeline (direct reply vs. tool vs. memory vs. plan)
- [ ] Context window management with automatic compression
- [ ] Multi-step planning for complex requests
- [ ] Memory injection at runtime (semantic retrieval + episodic context)
- [ ] Procedural execution integration

### Phase 5: Advanced Features
- [ ] Memory dashboard (`/status` enhancement)
- [ ] Procedure editor TUI (`/edit-procedure`)
- [ ] Audit log viewer (`/audit`)
- [ ] CLI subcommands (chat, memory, tools, procedures)
- [ ] Import/export utilities
- [ ] Multi-device encrypted sync

### Phase 6: Polish
- [ ] Memory curation UI (review episodic summaries, edit semantic facts)
- [ ] Conversation threading / multi-session support
- [ ] Performance optimization (vector search, connection pooling)
- [ ] Documentation and example MCP servers
- [ ] Integration tests end-to-end

---

## 11. Open Questions

1. **Vector embeddings:** Do we run embeddings locally (e.g., `nomic-embed-text` via Ollama) or outsource to the LLM? Local is more private but uses resources.
2. **Context window compression:** Summarize with the LLM (quality) or use a dedicated compression model (speed)? Or use sliding window + episodic push as the first pass?
3. **Procedural memory format:** Pure YAML (human-readable, limited logic) or Python modules (full logic, harder to audit)? Hybrid approach: YAML metadata + optional Python script reference?
4. **Cloud sync:** If included, what's the sync strategy? Git-backed? Custom protocol? Third-party (encrypted S3, Matrix)?
5. **Model routing:** The `# todo use a model router` — should different tasks use different models? (e.g., small model for summarization, large for reasoning)
6. **Multi-agent:** Could the orchestrator spawn sub-agents for parallel tasks? Or is that over-engineering for v1?

---

*Draft: 2025-09-10 · Status: v0.1 · Ready for iteration*
