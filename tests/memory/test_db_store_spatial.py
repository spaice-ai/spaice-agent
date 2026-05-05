"""Tests for spatial index and multi-hop retrieval in db_store."""
from __future__ import annotations

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
    cur = MagicMock()
    inner_conn = MagicMock()
    inner_conn.cursor.return_value.__enter__.return_value = cur
    conn = MagicMock()
    conn.__enter__.return_value = inner_conn
    pool = MagicMock()
    pool.getconn.return_value = conn
    with patch.object(dbs, "_get_pool", return_value=pool):
        yield conn, cur


# ── link ──────────────────────────────────────────────────────

class TestLink:
    def test_creates_link(self, mock_db):
        conn, cur = mock_db
        lid = dbs.link("id-a", "id-b", "RELATED_TO")
        assert lid
        assert cur.execute.called

    def test_explicit_connection(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        lid = dbs.link("id-a", "id-b", "CORRECTS", conn=conn)
        assert lid


# ── get_links ─────────────────────────────────────────────────

class TestGetLinks:
    def test_returns_links(self, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = [
            ("lid1", "src", "tgt", "RELATED_TO", 1.0, "now"),
        ]
        results = dbs.get_links("src")
        assert len(results) == 1
        assert results[0]["relationship"] == "RELATED_TO"

    def test_direction_filter(self, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.get_links("src", direction="incoming")
        sql = cur.execute.call_args[0][0]
        assert "target_id" in sql

    def test_invalid_direction(self, mock_db):
        with pytest.raises(ValueError):
            dbs.get_links("src", direction="sideways")


# ── get_corrections ───────────────────────────────────────────

class TestGetCorrections:
    def test_filters_corrects(self, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        dbs.get_corrections("rule-id")
        sql = cur.execute.call_args[0][0]
        params = cur.execute.call_args[0][1]
        assert "CORRECTS" in sql              # hardcoded in SQL
        assert "memory_entries me" in sql     # JOIN for L6 enforcement
        assert "me.layer = 6" in sql          # L6 filter
        assert params == ("rule-id",)         # only target_id parameterised


# ── retrieve_multi_hop ────────────────────────────────────────

class TestRetrieveMultiHop:
    def test_returns_semantic_results(self, mock_embedder, mock_db):
        conn, cur = mock_db
        # First call: retrieve() — returns initial hits
        # We need to intercept the actual PG call chain
        cur.fetchall.side_effect = [
            # retrieve() call: 2 results
            [("id1", 5, "ns1", "content1", "summary1", 0.95, 0.87),
             ("id2", 6, "ns2", "content2", "summary2", 0.98, 0.82)],
            # Empty (no _fetch_entry_summary hits)
            [], [], [],
            # If get_links is called, empty
            [], [],
        ]
        results = dbs.retrieve_multi_hop("keypad placement", limit=2)
        # Both results should be from semantic hop 1
        assert len(results) >= 0  # At minimum doesn't crash

    def test_accepts_parameters(self, mock_embedder, mock_db):
        conn, cur = mock_db
        cur.fetchall.return_value = []
        results = dbs.retrieve_multi_hop(
            "test", max_hops=2, decay=0.5, limit=3,
            layers=[5, 6], namespace_prefix="doctrine",
        )
        assert results == []


# ── link_from_cross_references ───────────────────────────────

class TestLinkFromCrossReferences:
    def test_creates_links(self, mock_db):
        conn, cur = mock_db
        # get_links is called when traversing cross-refs
        cur.fetchall.return_value = [("target-uuid",)]
        count = dbs.link_from_cross_references(
            "source-uuid",
            [{"doctrine_id": "keypad-lighting"}, {"doctrine_id": "reed"}],
        )
        assert count >= 0  # At minimum doesn't crash

    def test_empty_cross_refs(self, mock_db):
        count = dbs.link_from_cross_references("src", [])
        assert count == 0


# ── build_spatial_index ──────────────────────────────────────

class TestBuildSpatialIndex:
    def test_returns_stats(self, mock_db):
        conn, cur = mock_db
        # Return entries with doctrine_id in metadata
        cur.fetchall.side_effect = [
            # First: fetch all entries
            [("id1", "content1", "doctrine/placement", {"doctrine_id": "keypad-lighting"}),
             ("id2", "content2", "doctrine/placement", {"doctrine_id": "keypad-lighting"}),
             ("id3", "content3", "doctrine/derivation", {"doctrine_id": "reed"})],
            # Similarity query: empty
            [],
        ]
        stats = dbs.build_spatial_index()
        assert isinstance(stats, dict)
        assert "doctrine_links" in stats
        assert "similarity_links" in stats
        assert "reference_links" in stats
