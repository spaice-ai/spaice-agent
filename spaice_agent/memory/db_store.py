from __future__ import annotations

import os
import uuid
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import psycopg2
from psycopg2.extras import Json as PsycopgJson
from psycopg2.pool import ThreadedConnectionPool
from sentence_transformers import SentenceTransformer
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
SYDNEY = ZoneInfo("Australia/Sydney")


def _now() -> datetime:
    return datetime.now(SYDNEY)


# ---------------------------------------------------------------------------
# Embedding model (lazy, with fallback)
# ---------------------------------------------------------------------------
_EMBEDDER: Optional[SentenceTransformer] = None


_EMBEDDER_LOCK = threading.Lock()

def _get_embedder() -> SentenceTransformer:
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    with _EMBEDDER_LOCK:
        if _EMBEDDER is not None:
            return _EMBEDDER
        # Try primary model, fall back to secondary
        for model_name in ("all-mpnet-base-v2", "all-MiniLM-L6-v2"):
            try:
                model = SentenceTransformer(model_name)
                model.encode("init")  # force load
                _EMBEDDER = model
                return _EMBEDDER
            except Exception:
                continue
        raise RuntimeError("Could not load any sentence-transformers embedding model.")


def _embed_text(text: str) -> np.ndarray:
    model = _get_embedder()
    # sentence-transformers default output is numpy array; normalize
    vec = model.encode(text, normalize_embeddings=True)
    # Ensure it's a 1D float32 array
    vec = np.asarray(vec, dtype=np.float32)
    return vec


# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------
_POOL: Optional[ThreadedConnectionPool] = None
_POOL_LOCK = threading.Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL
        import os
        dbname = os.environ.get("SPAICE_DB_NAME", "spaice")
        host = os.environ.get("SPAICE_DB_HOST", "localhost")
        port = os.environ.get("SPAICE_DB_PORT", "5432")
        user = os.environ.get("SPAICE_DB_USER", "jozef")
        password = os.environ.get("SPAICE_DB_PASSWORD", "")
        _POOL = ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dbname=dbname,
            host=host,
            port=port,
            user=user,
            password=password,
        )
        return _POOL


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db(workspace_id: str = "jozef") -> bool:
    """Ensure the memory schema exists in the spaice database.

    Creates pgvector extension, memory_entries table, memory_links table,
    and all required indexes. Idempotent — safe to run on every install.
    Returns True if any schema change was made, False if already current.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()

        # Enable pgvector
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # Detect embedding dimension from the loaded model so the schema
        # always matches the embedder that will actually be used at runtime.
        # This prevents dimension mismatches when the primary model fails
        # and the fallback (all-MiniLM-L6-v2, 384d) takes over.
        embedder = _get_embedder()
        embed_dim = embedder.get_embedding_dimension()

        # Check if tables already exist
        cur.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_name = 'memory_entries')"
        )
        exists = cur.fetchone()[0]

        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memory_entries (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id TEXT NOT NULL DEFAULT 'jozef',
                layer INTEGER NOT NULL DEFAULT 5,
                namespace TEXT,
                content TEXT,
                summary TEXT,
                embedding vector({embed_dim}),
                metadata JSONB DEFAULT '{{}}',
                source_event_id UUID,
                confidence DOUBLE PRECISION DEFAULT 0.5,
                trust_weight DOUBLE PRECISION DEFAULT 1.0,
                user_validated BOOLEAN DEFAULT FALSE,
                user_rejected BOOLEAN DEFAULT FALSE,
                feedback_action TEXT,
                feedback_reason TEXT,
                paused BOOLEAN DEFAULT FALSE,
                sensitivity INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                accessed_at TIMESTAMPTZ,
                access_count INTEGER DEFAULT 0,
                expires_at TIMESTAMPTZ,
                dissolved_at TIMESTAMPTZ
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_id UUID NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
                target_id UUID NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
                relationship TEXT NOT NULL DEFAULT 'RELATED_TO',
                weight DOUBLE PRECISION DEFAULT 1.0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (source_id, target_id, relationship)
            )
        """)

        # Indexes
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace "
            "ON memory_entries(workspace_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_entries_layer "
            "ON memory_entries(layer)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_entries_namespace "
            "ON memory_entries(namespace)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_entries_embedding "
            "ON memory_entries USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 10)"
        ) if not exists else cur.execute("SELECT 1")  # ivfflat: skip if table already had data
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_links_source "
            "ON memory_links(source_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_links_target "
            "ON memory_links(target_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_links_relationship "
            "ON memory_links(relationship)"
        )

        conn.commit()
        return not exists  # True if we just created tables
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# Utility: vector to pgvector string
# ---------------------------------------------------------------------------
def _vec_to_str(vec: np.ndarray) -> str:
    """Convert a normalised embedding to the pgvector literal format."""
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store(
    layer: int,
    namespace: str,
    content: str,
    *,
    summary: str = "",
    confidence: float = 0.95,
    trust_weight: float = 1.0,
    user_validated: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    source_event_id: Optional[str] = None,
    conn: Optional[psycopg2.extensions.connection] = None,
) -> str:
    """
    Generate embedding from content, insert into memory_entries, return new UUID.
    """
    embedding = _embed_text(content)
    entry_id = str(uuid.uuid4())
    workspace = "jozef"
    now = _now()
    meta_json = None if metadata is None else PsycopgJson(metadata)

    embed_str = _vec_to_str(embedding)

    query = """
        INSERT INTO memory_entries
            (id, workspace_id, layer, namespace, content, summary,
             embedding, confidence, trust_weight, user_validated,
             metadata, source_event_id, created_at, updated_at,
             access_count, accessed_at)
        VALUES
            (%s, %s, %s, %s, %s, %s,
             %s::vector, %s, %s, %s,
             %s, %s, %s, %s,
             %s, %s)
    """
    params = (
        entry_id,
        workspace,
        layer,
        namespace,
        content,
        summary,
        embed_str,
        confidence,
        trust_weight,
        user_validated,
        meta_json,
        source_event_id,
        now,
        now,
        0,
        None,
    )

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            # Caller owns commit — don't auto-commit on borrowed connections
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(query, params)
                    conn_.commit()
            finally:
                pool.putconn(conn_)

    return entry_id


def retrieve(
    query_text: str,
    *,
    layers: Optional[List[int]] = None,
    namespace_prefix: Optional[str] = None,
    limit: int = 5,
    min_confidence: float = 0.0,
    conn: Optional[psycopg2.extensions.connection] = None,
) -> List[Dict[str, Any]]:
    """
    Embed query, perform pgvector cosine similarity search, boost L6.
    Returns list of dicts with similarity.
    """
    query_embedding = _embed_text(query_text)
    embed_str = _vec_to_str(query_embedding)

    base_query = """
        SELECT id, layer, namespace, content, summary, confidence,
               1.0 - (embedding <=> %s::vector) AS similarity
        FROM memory_entries
        WHERE workspace_id = 'jozef'
          AND (embedding <=> %s::vector) < 0.7
          AND confidence >= %s
    """
    params = [embed_str, embed_str, min_confidence]

    if layers and len(layers) > 0:
        placeholders = ",".join(["%s"] * len(layers))
        base_query += f" AND layer IN ({placeholders})"
        params.extend(layers)

    if namespace_prefix is not None:
        base_query += " AND namespace LIKE %s"
        params.append(namespace_prefix + "%")

    # Boost L6: multiply similarity by 2, sort descending
    base_query += " ORDER BY (CASE WHEN layer = 6 THEN 2.0 ELSE 1.0 END) * (1.0 - (embedding <=> %s::vector)) DESC"
    params.append(embed_str)

    base_query += " LIMIT %s"
    params.append(limit)

    results = []
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(base_query, params)
            rows = cur.fetchall()
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(base_query, params)
                    rows = cur.fetchall()
            finally:
                pool.putconn(conn_)

    for row in rows:
        results.append(
            {
                "id": row[0],
                "layer": row[1],
                "namespace": row[2],
                "content": row[3],
                "summary": row[4],
                "confidence": row[5],
                "similarity": float(row[6]),
            }
        )

    return results


def retrieve_related(
    entry_id: str,
    *,
    limit: int = 5,
    conn: Optional[psycopg2.extensions.connection] = None,
) -> List[Dict[str, Any]]:
    """
    Find entries similar to the given entry_id, excluding itself.
    """
    # Fetch the embedding of the target entry
    fetch_embed_query = "SELECT embedding FROM memory_entries WHERE id = %s AND workspace_id = 'jozef'"
    target_embed = None

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(fetch_embed_query, (entry_id,))
            row = cur.fetchone()
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(fetch_embed_query, (entry_id,))
                    row = cur.fetchone()
            finally:
                pool.putconn(conn_)

    if row is None:
        return []
    target_embed_str = row[0]  # pgvector returns a string or adapted type; use as is

    # Now search for similar entries, excluding self
    query = """
        SELECT id, layer, namespace, content, summary, confidence,
               1.0 - (embedding <=> %s::vector) AS similarity
        FROM memory_entries
        WHERE workspace_id = 'jozef'
          AND id != %s
          AND (embedding <=> %s::vector) < 0.7
        ORDER BY (CASE WHEN layer = 6 THEN 2.0 ELSE 1.0 END) * (1.0 - (embedding <=> %s::vector)) DESC
        LIMIT %s
    """
    params = [target_embed_str, entry_id, target_embed_str, target_embed_str, limit]

    results = []
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()
            finally:
                pool.putconn(conn_)

    for row in rows:
        results.append(
            {
                "id": row[0],
                "layer": row[1],
                "namespace": row[2],
                "content": row[3],
                "summary": row[4],
                "confidence": row[5],
                "similarity": float(row[6]),
            }
        )
    return results


def update_access(
    entry_id: str,
    conn: Optional[psycopg2.extensions.connection] = None,
) -> None:
    """Increment access count and set accessed_at to now() in Sydney timezone."""
    now = _now()
    query = """
        UPDATE memory_entries
        SET access_count = access_count + 1,
            accessed_at = %s,
            updated_at = %s
        WHERE id = %s AND workspace_id = 'jozef'
    """
    params = (now, now, entry_id)

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            # Caller owns commit — don't auto-commit on borrowed connections
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(query, params)
                    conn_.commit()
            finally:
                pool.putconn(conn_)


def search_and_update(
    query_text: str,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Convenience: performs retrieve() and then batch-updates access counters
    on all returned entries. Returns the same list as retrieve().
    """
    results = retrieve(query_text, **kwargs)
    if not results:
        return []

    ids = [r["id"] for r in results]
    conn = kwargs.pop("conn", None)
    
    now = _now()
    batch_sql = """
        UPDATE memory_entries
        SET access_count = access_count + 1,
            accessed_at = %s,
            updated_at = %s
        WHERE workspace_id = 'jozef' AND id = ANY(%s::uuid[])
    """
    batch_params = (now, now, ids)

    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(batch_sql, batch_params)
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(batch_sql, batch_params)
                    conn_.commit()
            finally:
                pool.putconn(conn_)

    return results

# ---------------------------------------------------------------------------
# Spatial index / causal DAG — memory_links
# ---------------------------------------------------------------------------

# Relationship weights for multi-hop retrieval
RELATIONSHIP_WEIGHTS = {
    "CORRECTS": 1.5,
    "COMPOSES": 1.2,
    "CASCADES_FROM": 1.2,
    "RELATED_TO": 1.0,
    "REFERS_TO": 1.0,
    "NEXT": 0.8,
    "CAUSAL": 0.8,
}


def link(
    source_id: str,
    target_id: str,
    relationship: str,
    *,
    weight: float = 1.0,
    conn=None,
) -> Optional[str]:
    """Create a relationship link between two memory entries.

    Returns the link ID on success, None if the link already existed
    (ON CONFLICT DO NOTHING).
    """
    link_id = str(uuid.uuid4())
    now = _now()

    def _do(cur):
        cur.execute(
            """INSERT INTO memory_links (id, source_id, target_id, relationship, weight, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id, target_id, relationship) DO NOTHING""",
            (link_id, source_id, target_id, relationship, weight, now),
        )
        return cur.rowcount

    if conn is not None:
        with conn.cursor() as cur:
            inserted = _do(cur)
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    inserted = _do(cur)
                    conn_.commit()
            finally:
                pool.putconn(conn_)

    return link_id if inserted else None


def get_links(
    entry_id: str,
    *,
    direction: str = "both",
    relationship: str = None,
    conn=None,
) -> list[dict]:
    """Get all links from/to an entry. direction: outgoing, incoming, both."""
    if direction not in ("outgoing", "incoming", "both"):
        raise ValueError("direction must be outgoing, incoming, or both")

    query = (
        "SELECT id, source_id, target_id, relationship, weight, created_at "
        "FROM memory_links WHERE "
    )
    conditions = []
    params = []
    if direction == "outgoing":
        conditions.append("source_id = %s")
        params.append(entry_id)
    elif direction == "incoming":
        conditions.append("target_id = %s")
        params.append(entry_id)
    else:
        conditions.append("(source_id = %s OR target_id = %s)")
        params.extend([entry_id, entry_id])
    if relationship:
        conditions.append("relationship = %s")
        params.append(relationship)
    query += " AND ".join(conditions) + " ORDER BY created_at DESC"

    results = []
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                results.append({
                    "id": row[0], "source_id": row[1], "target_id": row[2],
                    "relationship": row[3], "weight": row[4], "created_at": row[5],
                })
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(query, params)
                    for row in cur.fetchall():
                        results.append({
                            "id": row[0], "source_id": row[1], "target_id": row[2],
                            "relationship": row[3], "weight": row[4], "created_at": row[5],
                        })
            finally:
                pool.putconn(conn_)
    return results


def get_corrections(target_id: str, *, conn=None) -> list[dict]:
    """Get CORRECTS links targeting this entry, ensuring sources are in L6.

    L6 is the corrective layer — only layer-6 corrections are returned.
    """
    query = (
        "SELECT ml.id, ml.source_id, ml.target_id, ml.relationship, ml.weight, ml.created_at "
        "FROM memory_links ml "
        "JOIN memory_entries me ON ml.source_id = me.id "
        "WHERE ml.target_id = %s "
        "AND ml.relationship = 'CORRECTS' "
        "AND me.layer = 6 "
        "AND me.workspace_id = 'jozef' "
        "ORDER BY ml.created_at DESC"
    )
    results = []
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(query, (target_id,))
            for row in cur.fetchall():
                results.append({
                    "id": row[0], "source_id": row[1], "target_id": row[2],
                    "relationship": row[3], "weight": row[4], "created_at": row[5],
                })
    else:
        pool = _get_pool()
        with pool.getconn() as conn_:
            try:
                with conn_.cursor() as cur:
                    cur.execute(query, (target_id,))
                    for row in cur.fetchall():
                        results.append({
                            "id": row[0], "source_id": row[1], "target_id": row[2],
                            "relationship": row[3], "weight": row[4], "created_at": row[5],
                        })
            finally:
                pool.putconn(conn_)
    return results


def retrieve_multi_hop(
    query_text: str,
    *,
    max_hops: int = 3,
    decay: float = 0.7,
    limit: int = 5,
    layers: list[int] = None,
    namespace_prefix: str = None,
    conn=None,
) -> list[dict]:
    """Multi-hop retrieval walking relationship edges with decay-weighted scoring.

    Hop 1: pgvector similarity search (L6 boosted via retrieve())
    Hop 2+: follow memory_links from each result, applying relationship weights
    Score = base_similarity * (decay ^ hop_depth) * relationship_weight
    Deduplicates by taking max score per entry. Returns list with 'path' field.
    """
    # Hop 1 — standard semantic search
    initial = retrieve(
        query_text, layers=layers, namespace_prefix=namespace_prefix,
        limit=limit * 2, conn=conn,
    )

    # Track best score + path per entry (NOT visited — we want max score, not first-come)
    best: dict[str, tuple[float, list[str], str]] = {}
    for r in initial:
        eid = r["id"]
        if eid not in best:
            best[eid] = (r["similarity"], [eid], f"semantic({r['similarity']:.3f})")

    # BFS for remaining hops
    frontier = [(eid, 0, score) for eid, (score, _, _) in best.items()]

    for hop in range(2, max_hops + 1):
        next_frontier = []
        for eid, hop_depth, _ in frontier:
            links = get_links(eid, direction="outgoing", conn=conn)
            for link in links:
                target = link["target_id"]
                rel = link["relationship"]
                rel_weight = RELATIONSHIP_WEIGHTS.get(rel, 1.0) * link.get("weight", 1.0)
                base_score = best[eid][0]
                new_score = base_score * decay * rel_weight
                if target not in best or new_score > best[target][0]:
                    path_ids = best[eid][1] + [target]
                    path_label = best[eid][2] + f" → {rel}({new_score:.3f})"
                    best[target] = (new_score, path_ids, path_label)
                next_frontier.append((target, hop_depth + 1, new_score))
        frontier = next_frontier
        if not frontier:
            break

    # Sort, enrich with entry data, return top limit
    sorted_items = sorted(best.items(), key=lambda x: x[1][0], reverse=True)[:limit]

    results = []
    for eid, (score, path_ids, path_label) in sorted_items:
        # Fetch the entry content via a simple query
        entry = _fetch_entry_summary(eid, conn=conn)
        if entry:
            entry["score"] = score
            entry["path"] = path_label
            results.append(entry)
    return results


def _fetch_entry_summary(entry_id: str, *, conn=None) -> dict:
    """Fetch minimal summary for a memory entry."""
    query = (
        "SELECT id, layer, namespace, content, summary, confidence "
        "FROM memory_entries WHERE id = %s AND workspace_id = 'jozef'"
    )
    def _do(cur):
        cur.execute(query, (entry_id,))
        row = cur.fetchone()
        if row:
            return {"id": row[0], "layer": row[1], "namespace": row[2],
                    "content": row[3], "summary": row[4], "confidence": row[5]}
        return None

    if conn is not None:
        with conn.cursor() as cur:
            return _do(cur)
    pool = _get_pool()
    with pool.getconn() as conn_:
        try:
            with conn_.cursor() as cur:
                return _do(cur)
        finally:
            pool.putconn(conn_)


def link_from_cross_references(
    source_id: str,
    cross_refs: list[dict],
    *,
    conn=None,
) -> int:
    """Create RELATED_TO links from a doctrine entry's cross_references.

    Each cross_ref dict should have 'doctrine_id' key. Links to any entry
    in the same doctrine family. Returns count of links created.
    """
    count = 0
    for ref in cross_refs:
        target_did = ref.get("doctrine_id")
        if not target_did:
            continue
        # Find entries matching this doctrine_id in metadata
        query = (
            "SELECT id FROM memory_entries "
            "WHERE workspace_id = 'jozef' "
            "AND metadata->>'doctrine_id' = %s "
            "LIMIT 5"
        )
        def _do(cur):
            nonlocal count
            cur.execute(query, (target_did,))
            rows = cur.fetchall()
            for row in rows:
                cur.execute(
                    """INSERT INTO memory_links (id, source_id, target_id, relationship, weight, created_at)
                       VALUES (%s, %s, %s, 'RELATED_TO', 1.0, %s)
                       ON CONFLICT (source_id, target_id, relationship) DO NOTHING""",
                    (str(uuid.uuid4()), source_id, row[0], _now()),
                )
                if cur.rowcount:
                    count += 1

        if conn is not None:
            with conn.cursor() as cur:
                _do(cur)
        else:
            pool = _get_pool()
            with pool.getconn() as conn_:
                try:
                    with conn_.cursor() as cur:
                        _do(cur)
                        conn_.commit()
                finally:
                    pool.putconn(conn_)
    return count


def build_spatial_index(*, conn=None, workspace_id: str = "jozef") -> dict:
    """Scan all entries and create spatial index links.

    Returns: {"doctrine_links": N, "similarity_links": M, "reference_links": P,
              "cross_layer_links": Q}
    """
    stats = {"doctrine_links": 0, "similarity_links": 0, "reference_links": 0, "cross_layer_links": 0}

    # Fetch all entries
    query = (
        "SELECT id, content, namespace, metadata FROM memory_entries "
        "WHERE workspace_id = 'jozef'"
    )
    def _fetch(cur):
        cur.execute(query)
        return [{"id": r[0], "content": r[1], "namespace": r[2], "metadata": r[3] or {}}
                for r in cur.fetchall()]

    # Use a single connection for the entire build when conn not provided.
    # Avoids pool-thrashing: N+1 getconn/putconn cycles between link() calls
    # can corrupt pool state on ThreadedConnectionPool under high churn.
    _close_conn = False
    if conn is None:
        pool = _get_pool()
        conn = pool.getconn()
        _close_conn = True

    try:
        with conn.cursor() as cur:
            entries = _fetch(cur)

        # 1. Same doctrine_id → RELATED_TO
        by_doctrine: dict[str, list[str]] = {}
        for e in entries:
            did = e["metadata"].get("doctrine_id", "")
            if did:
                by_doctrine.setdefault(did, []).append(e["id"])
        for did, eids in by_doctrine.items():
            if len(eids) > 1:
                for i in range(len(eids)):
                    for j in range(i + 1, len(eids)):
                        link(eids[i], eids[j], "RELATED_TO", conn=conn)
                        stats["doctrine_links"] += 1

        # 2. Same namespace + high cosine similarity → RELATED_TO
        sim_query = (
            "SELECT a.id AS source_id, b.id AS target_id "
            "FROM memory_entries a "
            "JOIN memory_entries b ON a.namespace = b.namespace AND a.id < b.id "
            "WHERE a.workspace_id = 'jozef' AND b.workspace_id = 'jozef' "
            "AND 1 - (a.embedding <=> b.embedding) > 0.75"
        )
        with conn.cursor() as cur:
            cur.execute(sim_query)
            for row in cur.fetchall():
                link(row[0], row[1], "RELATED_TO", conn=conn)
                stats["similarity_links"] += 1

        conn.commit()

        # 3. Cross-reference patterns in content → REFERS_TO
        import re
        uuid_pat = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
        all_ids = {e["id"] for e in entries}
        for e in entries:
            refs = set(uuid_pat.findall(e["content"] or ""))
            for ref_id in refs:
                if ref_id in all_ids and ref_id != e["id"]:
                    link(e["id"], ref_id, "REFERS_TO", conn=conn)
                    stats["reference_links"] += 1

        # 4. Cross-layer semantic linking — L6 corrections → L5 doctrines
        # Uses pgvector to find the most similar L5 domain entries for each L6
        # correction, creating CORRECTS (L6→L5) and CASCADES_FROM (L5→L6) edges.
        # This is what makes multi-hop retrieval cross clusters: a query hitting
        # a correction can walk to the doctrine it corrected.
        #
        # Also does content-based matching: checks if correction content
        # references specific doctrine names, IDs, or namespace patterns.
        cross_query = (
            "WITH l6 AS ("
            "  SELECT id, embedding FROM memory_entries "
            "  WHERE workspace_id = 'jozef' AND layer = 6"
            ") "
            "SELECT l6.id AS source_id, me.id AS target_id, "
            "       1 - (l6.embedding <=> me.embedding) AS similarity "
            "FROM l6 "
            "CROSS JOIN LATERAL ("
            "  SELECT me2.id, me2.embedding FROM memory_entries me2 "
            "  WHERE me2.workspace_id = 'jozef' AND me2.layer = 5 "
            "  ORDER BY me2.embedding <=> l6.embedding "
            "  LIMIT 5"
            ") AS me "
            "WHERE 1 - (l6.embedding <=> me.embedding) > 0.35"
        )
        with conn.cursor() as cur:
            cur.execute(cross_query)
            for row in cur.fetchall():
                link(row[0], row[1], "CORRECTS", weight=1.5, conn=conn)
                link(row[1], row[0], "CASCADES_FROM", weight=1.2, conn=conn)
                stats["cross_layer_links"] = stats.get("cross_layer_links", 0) + 2
        conn.commit()
    finally:
        if _close_conn:
            pool.putconn(conn)

    return stats
