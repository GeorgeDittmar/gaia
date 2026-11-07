# Technical Specification: Memoria Framework Implementation

## 1. System Overview
**Objective:** Build a modular Python library (`Memoria`) that acts as a middleware layer for LLM applications. It provides **episodic memory** (conversation history summaries) and **semantic memory** (dynamic Knowledge Graphs of user traits) to enable persistent, personalized conversations.

**High-Level Flow:**
`User Input` $\rightarrow$ **Memoria Engine** (Retrieve Context) $\rightarrow$ `LLM` $\rightarrow$ `Response` $\rightarrow$ **Memoria Engine** (Store & Update Memory).

---

## 2. Tech Stack & Dependencies
*   **Language:** Python 3.10+
*   **Database:** SQLite3 (for dev/prototyping), PostgreSQL (for production)
*   **Vector DB:** ChromaDB (lightweight, embeddable) or Pinecone
*   **Embeddings:** OpenAI `text-embedding-ada-002`
*   **LLM:** OpenAI GPT-4o-mini / GPT-4 (or compatible OpenAI API model)

---

## 3. Database Schema (SQL)
*Instruction for Coding Agent: Create a database manager module. The schema must strictly support the following tables.*

### **Table: `conversations`**
Stores the raw transcript.
*   `id` (INTEGER PK, Auto-increment)
*   `user_id` (TEXT)
*   `session_id` (TEXT)
*   `role` (TEXT: 'user' or 'assistant')
*   `content` (TEXT)
*   `timestamp` (DATETIME)

### **Table: `sessions`**
Tracks the summary of the active conversation.
*   `session_id` (TEXT PK)
*   `user_id` (TEXT)
*   `summary` (TEXT) — *The rolling context summary*
*   `created_at` (DATETIME)

### **Table: `knowledge_graph`**
Stores extracted facts (Triplets).
*   `triplet_id` (TEXT PK)
*   `user_id` (TEXT)
*   `subject` (TEXT)
*   `predicate` (TEXT)
*   `object` (TEXT)
*   `source_message_id` (INTEGER, FK to conversations)
*   `created_at` (DATETIME)

---

## 4. Core Modules (Class Structure)

### **A. `StorageManager`**
Handles all SQL and Vector DB interactions.
*   `store_message(user_id, session_id, role, content)`
*   `store_session_summary(session_id, summary)`
*   `store_triplet(user_id, subject, predicate, object, message_id)`
*   `retrieve_session_summary(session_id)`
*   `retrieve_user_knowledge(user_id, query_vector, top_k)`

### **B. `MemoryExtractor`**
Uses an LLM to parse raw user text into structured knowledge.
*   **Trigger:** Called *after* every user message.
*   **Task:** Extract "User Facts" (Subject, Predicate, Object).
*   **Constraint:** Only extract facts about the **user**, not general info.
*   **Prompt Template:**
    > "Extract facts from this user message as triplets (Subject, Predicate, Object). Only include information relevant to this user's persona, preferences, or history.
    > User: '{message}'
    > Triplets: JSON list"

### **C. `ContextRetrieval` (The Brain)**
Constructs the prompt context using the "Recency-Aware" weighting logic.
*   **Inputs:** Current User Message, User ID, Session ID.
*   **Steps:**
    1.  Retrieve `Session Summary` (Short-term context).
    2.  Retrieve `Knowledge Graph Triplets` (Long-term context) based on semantic similarity to the current message.
    3.  **Apply Recency Weighting** (Math Logic below).
    4.  Construct final prompt.

---

## 5. The "Secret Sauce": Recency Weighting Algorithm
*This is the core innovation. You must implement the exponential decay logic.*

**Logic:**
We calculate a weight for every retrieved triplet. Recent facts are more important; older facts fade unless they are core traits.

**Formula:**
$$ w_i = \frac{e^{-\alpha \cdot x_i}}{\sum e^{-\alpha \cdot x_j}} $$

*   $x_i$: Time (minutes) elapsed since triplet was created.
*   $\alpha$ (Alpha): Decay rate (Suggested start: `0.02`).

**Python Implementation Plan:**
1.  Fetch `top_k` triplets using vector similarity.
2.  For each triplet, calculate `minutes_since_creation`.
3.  Apply `math.exp(-alpha * minutes)`.
4.  Normalize so all weights sum to 1.0.
5.  Filter out triplets with weight < threshold (e.g., 0.001) to save tokens.

---

## 6. Implementation Workflow (The "Run" Loop)

When a user sends a message, the system executes this sequence:

### **Phase 1: Retrieval (Augmentation)**
1.  **Check Session:** Does a summary exist for `session_id`?
    *   *No:* Initialize empty summary.
2.  **Retrieve KG:** Query Vector DB for user triplets similar to the current message.
3.  **Calculate Weights:** Apply the Exponential Decay formula to the retrieved triplets.
4.  **Build Prompt:**
    ```text
    [SYSTEM]: You are an AI assistant.
    [CONTEXT - PREFERENCES]: {Top 5 weighted KG triplets}
    [CONTEXT - HISTORY]: {Current Session Summary}
    [USER]: {Current Message}
    ```

### **Phase 2: Generation**
1.  Send prompt to LLM.
2.  Receive `Response`.

### **Phase 3: Persistence (Learning)**
1.  **Save Transcript:** Insert raw user message and assistant response to `conversations` table.
2.  **Extract Knowledge:** Call `MemoryExtractor` on the *user's* message to find new triplets.
3.  **Update KG:**
    *   Insert new triplets into SQL + Vector DB.
    *   *Conflict Resolution (Advanced):* If a new triplet contradicts an old one (e.g., "I like coffee" vs "I hate coffee"), the new one has a weight of 1.0, overriding the old one in future retrievals.
4.  **Update Summary:** Call LLM to update the `sessions` summary by combining the old summary + the new exchange.

---

## 7. Scenarios to Handle

1.  **New User, New Session:**
    *   Retrieve nothing.
    *   Prompt: Raw Message only.
    *   Post-action: Save to DB, extract first KG triplets.
2.  **Repeat User, New Session:**
    *   Retrieve KG Triplets (Weighted).
    *   Retrieve NO Session Summary (it's a new session).
    *   Prompt: Context + Message.
3.  **Repeat User, Ongoing Session:**
    *   Retrieve KG Triplets (Weighted).
    *   Retrieve Session Summary.
    *   Prompt: Summary + Context + Message.

---

## 8. Deliverables Checklist
1.  [ ] **`database.py`**: SQLite/ChromaDB connection and schema migration.
2.  [ ] **`retriever.py`**: Logic to query vectors and apply the math formula.
3.  [ ] **`extractor.py`**: Prompting logic for triplet extraction.
4.  [ ] **`memoria.py`**: The main orchestration class that ties it all together.
5.  [ ] **`main.py`**: A demo script simulating a conversation with multiple turns and user sessions.
