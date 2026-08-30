import json
import tempfile
from pathlib import Path
from dual_index.shard import ShardStore


def test_put_get_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 4)
        s.put("alice", {"username": "alice", "uid": 1, "format": "single"})
        assert s.get("alice") == {"username": "alice", "uid": 1, "format": "single"}


def test_get_absent_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 4)
        assert s.get("nobody") is None


def test_shards_returns_correct():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 7)
        assert s.shards() == 7


def test_put_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 4)
        s.put("alice", {"username": "alice", "uid": 1, "format": "single"})
        s.put("alice", {"username": "alice", "uid": 2, "format": "single"})
        assert s.get("alice")["uid"] == 2


def test_stable_hash_determinism_across_instances():
    with tempfile.TemporaryDirectory() as tmp:
        s1 = ShardStore(tmp, 8)
        s1.put("alice", {"username": "alice", "uid": 100, "format": "single"})
        s1.put("bob", {"username": "bob", "uid": 200, "format": "single"})
        # New instance should see same data
        s2 = ShardStore(tmp, 8)
        assert s2.get("alice") == {"username": "alice", "uid": 100, "format": "single"}
        assert s2.get("bob") == {"username": "bob", "uid": 200, "format": "single"}


def test_shard_files_created_under_base_dir():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 3)
        s.put("alice", {"username": "alice", "uid": 1, "format": "single"})
        files = list(Path(tmp).glob("*.json"))
        assert len(files) >= 1


def test_multiple_users_different_shards():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 16)
        for name in ["alice", "bob", "charlie", "david"]:
            s.put(name, {"username": name, "uid": 1, "format": "single"})
        for name in ["alice", "bob", "charlie", "david"]:
            assert s.get(name) is not None


def test_preserves_dict_exactly():
    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 4)
        rec = {
            "username": "alice2",
            "ig_uid": 10,
            "threads_uid": 20,
            "link_state": "linked",
            "format": "dual",
        }
        s.put("alice2", rec)
        assert s.get("alice2") == rec


def test_shard_store_rejects_invalid_num_shards():
    import pytest

    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            ShardStore(tmp, 0)
        with pytest.raises(ValueError):
            ShardStore(tmp, -1)
        with pytest.raises(ValueError):
            ShardStore(tmp, True)
        with pytest.raises(ValueError):
            ShardStore(tmp, 3.5)
