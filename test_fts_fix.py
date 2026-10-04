"""Quick test for FTS fix: memory persistence + question mark handling."""
import asyncio
import os
from gaia.core.memory.sqlite_store import SQLiteMemoryStore


async def test():
    if os.path.exists("gaia-memory.db"):
        os.remove("gaia-memory.db")

    store = SQLiteMemoryStore("gaia-memory.db")
    await store.initialize()

    await store.semantic_insert(
        content="My name is George Dittmar and I live in Portland Oregon",
        category="preference",
        confidence=1.0,
        source="user",
    )

    await store.close()

    # Simulate restart
    store2 = SQLiteMemoryStore("gaia-memory.db")
    await store2.initialize()

    count = store2._conn.execute("SELECT COUNT(*) FROM semantic_facts").fetchone()[0]
    print(f"Facts after restart: {count}")

    queries = [
        "what is my name?",
        "who am I?",
        "tell me about Portland?",
        "what do you know about me?",
        "my name",
        "Portland",
        "George",
        "dog",
    ]
    for q in queries:
        try:
            results = await store2.semantic_search(q, limit=5)
            if results:
                print(
                    f'  {q!r:35s} -> {len(results)} | '
                    f'"{results[0]["content"][:50]}..."'
                )
            else:
                print(f"  {q!r:35s} -> 0 results")
        except Exception as e:
            print(f"  {q!r:35s} -> ERROR: {e}")

    await store2.close()


if __name__ == "__main__":
    asyncio.run(test())
