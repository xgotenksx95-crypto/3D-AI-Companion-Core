"""Aki Memory-Modul – SQLite-Backend mit semantischer Suche (sqlite-vec + transformers, ohne sentence-transformers)."""

import sqlite3
import sqlite_vec
import struct
import datetime
import os
import threading
import torch
from transformers import AutoTokenizer, AutoModel
# in process/memory_func/memory.py, innerhalb extract_facts_llm():
from process.llm_func.llm import get_response
_path = None
_cfg = None
_conn = None
_tokenizer = None
_model = None
_lock = threading.RLock()

EMBEDDING_DIM = 384  # Dimension von all-MiniLM-L6-v2
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def init(memory_path: str, config: dict):
    """Öffnet (oder erstellt) die SQLite-Datenbank, lädt sqlite-vec und das Embedding-Modell."""
    global _path, _cfg, _conn
    _path = memory_path
    _cfg = config

    _conn = sqlite3.connect(_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.enable_load_extension(True)
    sqlite_vec.load(_conn)
    _conn.enable_load_extension(False)

    _create_tables()

    print("🧠 Lade Embedding-Modell für semantische Suche...")
    global _tokenizer, _model
    _tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
    _model = AutoModel.from_pretrained(EMBEDDING_MODEL_NAME)
    _model.eval()
    print("✅ Embedding-Modell geladen!")


def _embed(text: str) -> list:
    """Erzeugt einen 384-dimensionalen Embedding-Vektor für einen Text (Mean Pooling über Token-Embeddings)."""
    inputs = _tokenizer(text, padding=True, truncation=True, return_tensors="pt")
    with torch.no_grad():
        output = _model(**inputs)
    token_embeddings = output.last_hidden_state
    mask = inputs["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
    summed = torch.sum(token_embeddings * mask, dim=1)
    counts = torch.clamp(mask.sum(dim=1), min=1e-9)
    mean_pooled = summed / counts
    return mean_pooled[0].tolist()


def _create_tables():
    with _lock:
        _conn.execute("""CREATE TABLE IF NOT EXISTS profile (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        _conn.execute("""CREATE TABLE IF NOT EXISTS interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            interest TEXT UNIQUE
        )""")
        _conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        _conn.execute(f"""CREATE VIRTUAL TABLE IF NOT EXISTS message_vectors USING vec0(
            embedding float[{EMBEDDING_DIM}]
        )""")
        _conn.execute("""CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        _conn.execute(f"""CREATE VIRTUAL TABLE IF NOT EXISTS fact_vectors USING vec0(
            embedding float[{EMBEDDING_DIM}]
        )""")
        _conn.commit()


def _serialize(vector) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _get_profile(key: str, default: str = "") -> str:
    with _lock:
        row = _conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def _set_profile(key: str, value: str):
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)", (key, value))
        _conn.commit()


def get_profile_dict() -> dict:
    """Snapshot-Dict, kompatibel zur alten `data`-Struktur (z.B. für den System-Prompt)."""
    with _lock:
        interests = [r["interest"] for r in _conn.execute(
            "SELECT interest FROM interests ORDER BY id DESC LIMIT 10").fetchall()]
        total = _conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    return {
        "user_name": _get_profile("user_name"),
        "favorite_anime": _get_profile("favorite_anime"),
        "interests": interests,
        "total_messages": total,
    }


class _MemoryDict(dict):
    """Kompatibilitäts-Objekt: erlaubt weiterhin memory["user_name"] etc.,
    liest/schreibt aber transparent in die SQLite-DB statt in ein echtes dict."""

    def __getitem__(self, key):
        if key == "user_name":
            return _get_profile("user_name")
        if key == "favorite_anime":
            return _get_profile("favorite_anime")
        if key == "interests":
            with _lock:
                return [r["interest"] for r in _conn.execute(
                    "SELECT interest FROM interests ORDER BY id DESC LIMIT 10").fetchall()]
        if key == "recent_messages":
            limit = _cfg.get("recent_messages_count", 5) if _cfg else 5
            with _lock:
                rows = _conn.execute(
                    "SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [
                {"role": r["role"], "content": r["content"], "timestamp": r["created_at"]}
                for r in reversed(rows)
            ]
        if key == "total_messages":
            with _lock:
                return _conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key == "user_name" or key == "favorite_anime":
            _set_profile(key, value)
        elif key == "interests":
            with _lock:
                _conn.execute("DELETE FROM interests")
                for interesse in value:
                    _conn.execute("INSERT OR IGNORE INTO interests (interest) VALUES (?)", (interesse,))
                _conn.commit()
        else:
            super().__setitem__(key, value)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


data = _MemoryDict()


def save():
    """Kompatibilitäts-Funktion – jede Schreibung committet bereits sofort, daher No-Op."""
    pass


def backup():
    """Erstellt eine zeitgestempelte Kopie der gesamten Datenbank."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = _path.replace(".db", f"_backup_{timestamp}.db")
    try:
        with _lock:
            backup_conn = sqlite3.connect(backup_path)
            _conn.backup(backup_conn)
            backup_conn.close()
        print(f"💾 Backup erstellt: {os.path.basename(backup_path)}")
    except sqlite3.Error as e:
        print(f"⚠️  Backup fehlgeschlagen: {e}")


def add_message(role: str, content: str):
    """Fügt eine Nachricht hinzu, erzeugt ihr Embedding und verwaltet Backup-Logik."""
    now = datetime.datetime.now().isoformat()
    with _lock:
        cursor = _conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, now)
        )
        message_id = cursor.lastrowid

        if _model:
            embedding = _embed(content)
            _conn.execute(
                "INSERT INTO message_vectors(rowid, embedding) VALUES (?, ?)",
                (message_id, _serialize(embedding))
            )

        _conn.commit()
        total = _conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    if total % _cfg["backup_threshold"] == 0:
        backup()


def extract_from_text(text: str, chat_history: list, get_system_prompt_fn):
    """Liest Nutzerinfos aus dem Text und speichert sie."""
    lower = text.lower()

    if "ich heiße" in lower or "mein name ist" in lower:
        try:
            key = "ich heiße" if "ich heiße" in lower else "mein name ist"
            name = lower.split(key, 1)[1].strip().split()[0].capitalize()
            _set_profile("user_name", name)
            chat_history[0]["content"] = get_system_prompt_fn()
            print(f"[Memory]: Name gespeichert → {name}")
        except IndexError:
            pass

    if "lieblingsanime" in lower or "lieblings anime" in lower:
        try:
            key = "lieblingsanime" if "lieblingsanime" in lower else "lieblings anime"
            anime = text.split(key, 1)[1].strip().lstrip("ist").strip().rstrip(".")
            _set_profile("favorite_anime", anime)
            chat_history[0]["content"] = get_system_prompt_fn()
            print(f"[Memory]: Anime gespeichert → {anime}")
        except IndexError:
            pass

    for key in ["ich mag", "ich liebe", "ich schaue gerne", "ich spiele gerne"]:
        if key in lower:
            try:
                interesse = text.split(key, 1)[1].strip().rstrip(".")
                if interesse:
                    with _lock:
                        _conn.execute("INSERT OR IGNORE INTO interests (interest) VALUES (?)", (interesse,))
                        _conn.execute("""DELETE FROM interests WHERE id NOT IN (
                            SELECT id FROM interests ORDER BY id DESC LIMIT 10
                        )""")
                        _conn.commit()
                    print(f"[Memory]: Interesse gespeichert → {interesse}")
            except IndexError:
                pass


def build_context() -> str:
    limit = _cfg.get("recent_messages_count", 5)
    with _lock:
        rows = _conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    if not rows:
        return ""
    rows = list(reversed(rows))

    # Alte Formatierungs-Artefakte herausfiltern
    FILTER_PATTERNS = ["[INST]", "[/INST]", "[Respond in English only!]", "You MUST always respond"]
    lines = []
    for r in rows:
        content = r["content"]
        if any(p in content for p in FILTER_PATTERNS):
            continue  # Diese Nachricht überspringen
        who = "Aki" if r["role"] == "assistant" else "Nutzer"
        lines.append(f"{who}: {content}")

    if not lines:
        return ""
    return "\nLetzte Unterhaltung:\n" + "\n".join(lines)


def add_fact(content: str):
    """Speichert einen destillierten Fakt mit Embedding (für semantische Suche)."""
    now = datetime.datetime.now().isoformat()
    with _lock:
        cursor = _conn.execute(
            "INSERT INTO facts (content, created_at) VALUES (?, ?)",
            (content, now)
        )
        fact_id = cursor.lastrowid
        if _model:
            embedding = _embed(content)
            _conn.execute(
                "INSERT INTO fact_vectors(rowid, embedding) VALUES (?, ?)",
                (fact_id, _serialize(embedding))
            )
        _conn.commit()
    print(f"[Memory]: Fakt gespeichert → {content}")


FACT_EXTRACTION_PROMPT = """Extract any new personal facts about the user from their message below.
Only extract clear, factual statements about the user (name, preferences, hobbies, life details).
Output one fact per line, starting with "FACT: ", written in third person, short and clear.
If there is nothing worth remembering, output exactly: NONE

Example:
User message: My name is Vladimir and I really love Yu-Gi-Oh
Output:
FACT: The user's name is Vladimir.
FACT: The user loves Yu-Gi-Oh.

Example:
User message: What's the weather like today?
Output:
NONE""".strip()


def extract_facts_llm(text: str):
    """Nutzt das LLM selbst, um neue Fakten aus der Nutzer-Nachricht zu erkennen und zu speichern.
    Erkennt beliebige Formulierungen, statt auf feste Phrasen wie extract_from_text() angewiesen zu sein."""
    from process.llm_func.llm import get_response
    extraction_history = [
        {"role": "system", "content": FACT_EXTRACTION_PROMPT},
        {"role": "user", "content": text},
    ]
    result = get_response(extraction_history, temperature=0.2)
    if not result or "NONE" in result.upper():
        return

    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("FACT:"):
            fact = line[5:].strip()
            if fact:
                add_fact(fact)


def query_memory(query: str, limit: int = 5) -> str:
    """Tool-Funktion fürs LLM: durchsucht Erinnerungen SEMANTISCH (nach Bedeutung, nicht nur Wortlaut)."""
    if not _model:
        return "Semantische Suche nicht verfügbar."

    query_vec = _serialize(_embed(query))

    with _lock:
        knn_facts = _conn.execute(
            "SELECT rowid, distance FROM fact_vectors WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (query_vec, limit)
        ).fetchall()

        knn_messages = _conn.execute(
            "SELECT rowid, distance FROM message_vectors WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (query_vec, limit)
        ).fetchall()

        results = []
        for r in knn_facts:
            row = _conn.execute("SELECT content FROM facts WHERE id = ?", (r["rowid"],)).fetchone()
            if row:
                results.append(f"Fakt: {row['content']}")

        for r in knn_messages:
            row = _conn.execute(
                "SELECT role, content, created_at FROM messages WHERE id = ?", (r["rowid"],)
            ).fetchone()
            if row:
                who = "Aki" if row["role"] == "assistant" else "Nutzer"
                results.append(f"[{row['created_at']}] {who}: {row['content']}")

        interest_rows = _conn.execute(
            "SELECT interest FROM interests WHERE interest LIKE ?", (f"%{query}%",)
        ).fetchall()

    for r in interest_rows:
        results.insert(0, f"Interesse: {r['interest']}")

    if not results:
        return "Keine passenden Erinnerungen gefunden."
    return "\n".join(results)


def backfill_embeddings():
    """Einmalig aufzurufen: erzeugt Embeddings für bereits gespeicherte Nachrichten ohne Vektor."""
    if not _model:
        print("Embedder nicht initialisiert.")
        return

    with _lock:
        existing_ids = {r["rowid"] for r in _conn.execute("SELECT rowid FROM message_vectors").fetchall()}
        all_messages = _conn.execute("SELECT id, content FROM messages").fetchall()

        count = 0
        for row in all_messages:
            if row["id"] in existing_ids:
                continue
            embedding = _embed(row["content"])
            _conn.execute(
                "INSERT INTO message_vectors(rowid, embedding) VALUES (?, ?)",
                (row["id"], _serialize(embedding))
            )
            count += 1
        _conn.commit()

    print(f"Backfill abgeschlossen: {count} neue Embeddings erzeugt.")


def migrate_from_json(json_path: str):
    """Einmalige Migration der alten memory.json in die aktuelle SQLite-DB."""
    import json
    if not os.path.exists(json_path):
        print(f"Keine JSON-Datei gefunden unter: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        old = json.load(f)

    if old.get("user_name"):
        _set_profile("user_name", old["user_name"])
    if old.get("favorite_anime"):
        _set_profile("favorite_anime", old["favorite_anime"])

    with _lock:
        for interesse in old.get("interests", []):
            _conn.execute("INSERT OR IGNORE INTO interests (interest) VALUES (?)", (interesse,))

        for msg in old.get("recent_messages", []):
            _conn.execute(
                "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
                (
                    msg.get("role", "unknown"),
                    msg.get("content", ""),
                    msg.get("timestamp", datetime.datetime.now().isoformat()),
                )
            )
        _conn.commit()

    print(f"Migration abgeschlossen: {len(old.get('recent_messages', []))} Nachrichten übernommen.")
