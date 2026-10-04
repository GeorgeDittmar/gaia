# G.A.I.A. Memory Architecture

> Privacy-first, local, modular — three memory types behind a single storage protocol.

---

## Top-Level Flow

```mermaid
graph TB
    subgraph TUI["🖥️ GaiaTUI App"]
        direction TB
        CHAT[Chat Interface]
        MBROWSE[Memory Browser /memory]
        AVIEW[Audit Viewer /audit]
        TINSPECT[Tool Inspector /tools]
    end

    subgraph ORCH["⚙️ Agent Orchestrator"]
        direction TB
        DECIDE[Decision Pipeline]
        CONTEXT[Context Window Mgmt]
        PLAN[Multi-step Planner]
    end

    subgraph STORE["🗄️ MemoryStore Protocol"]
        direction LR
        SIFACE[semantic_insert / update / delete / search]
        EIFACE[episodic_record / search / get_conversation / prune]
        PIFACE[procedure_register / get / search]
        AIFACE[audit_log]
    end

    subgraph BACKENDS["💾 Storage Backends"]
        direction TB
        SQLITE[SQLite + SQLCipher]
        REDIS[Redis + RediSearch]
        FILE[JSON/YAML Files]
    end

    TUI --> ORCH
    ORCH --> STORE
    STORE --> BACKENDS

    DECIDE -.->|inject| CONTEXT
```

---

## Memory Types

```mermaid
graph LR
    subgraph SEM["🧠 Semantic Memory — What the user knows"]
        direction TB
        S1[semantic_facts]
        S1FTS[FTS5 virtual table]
        S1 --> S1FTS
        S2{content}
        S2 --> S2a[category / confidence / source]
        S2 --> S2b[source_id / created_at / updated_at]
        S2 --> S2c[metadata JSON]
        S1 --> S2
    end

    subgraph EPI["📖 Episodic Memory — What happened"]
        direction TB
        E1[episodic_events]
        E1FTS[FTS5 virtual table]
        E1 --> E1FTS
        E2{event_type / conversation_id}
        E2 --> E2a[timestamp / summary / payload JSON]
        E2 --> E2b[indexed_content for search]
        E2 --> E2c[created_at for retention]
        E1 --> E2
        E3[event_type CHECK]
        E3 --> E3a["conversation_start, turn, end"]
        E3 --> E3b["tool_call, tool_result, error"]
        E3 --> E3c["memory_op, config_change"]
        E3 --> E3d["mcp_connect, disconnect"]
        E2 --> E3
    end

    subgraph PROC["🔧 Procedural Memory — How to do things"]
        direction TB
        P1[procedure_entries]
        P1FTS[FTS5 virtual table]
        P1 --> P1FTS
        P2{name / description / category}
        P2 --> P2a[safety_level / prompt_template]
        P2 --> P2b[steps JSON / source_file]
        P2 --> P2c[enabled / version / metadata]
        P1 --> P2
    end

    SEM === EPI === PROC
```

---

## Audit Log

```mermaid
graph LR
    subgraph AUDIT["🔒 Audit Log — Append-only encrypted trail"]
        A1[audit_log]
        A1 --> A2{log_id / timestamp}
        A2 --> A3[action / actor]
        A3 --> A4[target_type / target_id]
        A4 --> A5[detail JSON / ip / user_agent]
        A6[Indexes: timestamp / action / target]
        A1 --> A6
    end

    ORCH -.->|log every non-trivial action| AUDIT
```

---

## Memory Type Characteristics

| Memory Type | Purpose | Key Features | Storage |
|---|---|---|---|
| **🧠 Semantic** | What the user knows | Confidence scoring, autodistillation, source-tracked | SQLite + FTS5 |
| **📖 Episodic** | What happened | Time-indexed, full conversation transcripts, retention policy | SQLite + FTS5 |
| **🔧 Procedural** | How to do things | Safety-gated (safe/caution/danger), hot-reloadable | YAML/JSON on disk + SQLite index |
| **🔒 Audit Log** | Action trail (metadata only) | Append-only, encrypted, exportable as signed JSONL | SQLite table |

---

## Storage Backends

| Backend | Status | Search | Features |
|---|---|---|---|
| **🗃️ SQLiteMemoryStore** | v1 default | FTS5 virtual tables | SQLCipher encryption, ACID, zero external deps |
| **☁️ RedisMemoryStore** | Planned v2 | RediSearch + HNSW vectors | EXPIRE TTL for retention, multi-device sync |
| **📁 FileStore** | Dev / lightweight | grep/ripgrep fallback | Human-editable, git-versionable, no DB |
