"""Tests for spaice_agent.memory.db_store — pgvector-backed 8-layer storage & retrieval."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import spaice_agent.memory.db_store as dbs


@pytest.fixture(autouse=True)
def reset_globals():
    dbs._POOL = None
    dbs._EMBEDDER = None
    yield


@pytest.fixture
def mock_embedder():
    vec = np.ones(768, dtype=np.float32) * 0.01
    emb = MagicMock()
    emb.encode.return_value = vec
    with patch.object(dbs, "_get_embedder", return_value=emb):
        yield emb


@pytest.fixture
def mock_db():
    """Mock pool + connection + cursor chain."""
    cur = MagicMock()
    inner_conn = MagicMock()
    inner_conn.cursor.return_value.__enter__.return_value = cur
    conn = MagicMock()
    conn.__enter__.return_value = inner_conn
    pool = MagicMock()
    pool.getconn.return_value = conn
    with patch.object(dbs, "_get_pool", return_value=pool):
        yield conn, cur


def _sql(cur):
    """Extract the SQL string from cur.execute mock call args (positional)."""
    return cur.execute.call_args[0][0]


def _params(cur):
    """Extract the params tuple from cur.execute mock call args."""
    return cur.execute.call_args[0][1]


# ── store ─────────────────────────────────────────────────────

class TestStore:
    def test_returns_uuid(self, mock_embedder, mock_db):
        conn, cur = mock_db
        eid = dbs.store(layer=5, namespace="test", content="hello world")
        uuid.UUID(eid)
        assert cur.execute.called

    def test_optional_fields(self, mock_embedder, mock_db):
        conn, cur = mock_db
        eid = dbs.store(
            layer=6, namespace="correction", content="fix",
            summary="Bug fix", confidence=0.98, trust_weight=0.9,
            user_validated=True, metadata={"source": "test"},
            source_event_id=str(uuid.uuid4()),
        )
        assert eid is not None

    def test_explicit_connection(self, mock_embedder):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        eid = dbs.store(layer=5, namespace="t", content="hi", conn=conn)
        assert eid

    def test_workspace(self, mock_embedder, mock_db):
        conn, cur = mock_db
        dbs.store(layer=3, namespace="client", content="prefs")
        params = _params(cur)
        assert params[1] == "jozef"


# ── retrieve ──────────────────────────────────────────────────

class TestRetrieve:
    def test_returns_list(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [("id1", 5, "ns", "c", "s", 0.95, 0.87)]
        results = dbs.retrieve("keypad placement")
        assert results[0]["id"] == "id1"
        assert results[0]["similarity"] == 0.87

    def test_filters_by_layers(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.retrieve("test", layers=[5, 6])
        assert "IN (%s,%s)" in _sql(cur)

    def test_filters_by_namespace(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.retrieve("test", namespace_prefix="doctrine")
        assert "LIKE" in _sql(cur)

    def test_boosts_L6(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.retrieve("correction")
        assert "layer = 6 THEN 2.0" in _sql(cur)

    def test_explicit_connection(self, mock_embedder):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        cur.fetchall.return_value = []
        results = dbs.retrieve("test", conn=conn)
        assert results == []

    def test_min_confidence(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.retrieve("test", min_confidence=0.8)
        assert 0.8 in _params(cur)


# ── retrieve_related ──────────────────────────────────────────

class TestRetrieveRelated:
    def test_returns_related(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = ("[0.1, 0.2]",)
        cur.fetchall.return_value = [("id2", 5, "ns", "c", "s", 0.9, 0.76)]
        results = dbs.retrieve_related("id1")
        assert results[0]["id"] == "id2"

    def test_missing_returns_empty(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchone.return_value = None
        results = dbs.retrieve_related("nonexistent")
        assert results == []


# ── update_access ─────────────────────────────────────────────

class TestUpdateAccess:
    def test_increments(self, mock_db):
        conn, cur = mock_db
        dbs.update_access("id1")
        assert "access_count + 1" in _sql(cur)

    def test_explicit_connection(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        dbs.update_access("id1", conn=conn)


# ── search_and_update ─────────────────────────────────────────

class TestSearchAndUpdate:
    def test_calls_retrieve_and_update(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            ("id1", 5, "ns", "c", "s", 0.9, 0.85),
            ("id2", 6, "corr", "c2", "s2", 0.95, 0.92),
        ]
        results = dbs.search_and_update("test query")
        assert len(results) == 2

    def test_empty(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        results = dbs.search_and_update("nothing")
        assert results == []
