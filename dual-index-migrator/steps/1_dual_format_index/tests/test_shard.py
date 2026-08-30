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


def test_same_shard_collocation_preserves_both():
    import hashlib

    with tempfile.TemporaryDirectory() as tmp:
        s = ShardStore(tmp, 2)
        # find two usernames hashing to same shard for num_shards=2
        candidates = [f"user{i}" for i in range(100)]
        buckets = {}
        for name in candidates:
            h = int(hashlib.md5(name.encode()).hexdigest(), 16) % 2
            buckets.setdefault(h, []).append(name)
        # pick a bucket with at least 2
        pair = next(v for v in buckets.values() if len(v) >= 2)[:2]
        a, b = pair[0], pair[1]
        s.put(a, {"username": a, "uid": 1, "format": "single"})
        s.put(b, {"username": b, "uid": 2, "format": "single"})
        assert s.get(a)["uid"] == 1
        assert s.get(b)["uid"] == 2
        # overwrite one should not delete the other
        s.put(a, {"username": a, "uid": 10, "format": "single"})
        assert s.get(a)["uid"] == 10
        assert s.get(b)["uid"] == 2
        # shard file should contain both
        import json

        shard_idx = int(hashlib.md5(a.encode()).hexdigest(), 16) % 2
        data = json.loads((Path(tmp) / f"shard_{shard_idx}.json").read_text())
        assert a in data and b in data
