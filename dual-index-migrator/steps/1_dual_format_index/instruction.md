Let's start with the foundation for an offline secondary index migration tool. The tool manages a sharded username index that exists in two formats: a legacy single-format where each username maps to a single uid, and a newer dual-format where each username maps to an IG uid, an optional Threads uid, and a link state. The full migration will come later; this step just sets up the core model.

Add the core model under /app/dual_index - you'll fill in these modules: Hidden tests will import the exact paths below via PYTHONPATH=/app, so keep the module and symbol names exactly as written.

| Module | Key symbols | Purpose |
|---|---|---|
| dual_index/encoding.py | encode_single, encode_dual, decode | Username validation and record format handling |
| dual_index/shard.py | ShardStore | File-backed storage split across shards |
| dual_index/store.py | DualIndex | Combines storage and encoding for single and dual records |
| dual_index/cli.py | python -m dual_index.cli | Command-line entry point invoked as python -m dual_index.cli |
| dual_index/__init__.py | - | Can be empty |

Encoding API - dual_index/encoding.py

| Function | Inputs | Returns | Validation |
|---|---|---|---|
| encode_single | username: str, uid: int (not bool) | dict with username, uid, format="single" | username lowercase 3-30 chars, a-z 0-9 _ . only, starts with letter; uid must be int and not bool (reject bool even though bool is subclass of int in Python); raises ValueError if invalid |
| encode_dual | username: str, ig_uid: int (not bool), threads_uid: int or None (not bool), link_state: str | dict with username, ig_uid, threads_uid, link_state, format="dual" | same username rules; link_state must be linked or unlinked; threads_uid must be an int (not bool) when linked and must be None when unlinked; ig_uid must be int not bool; raises ValueError otherwise |
| decode | record: dict | normalized dict | raises ValueError if required keys are missing or types are wrong, or if uid/ig_uid/threads_uid is bool |

Storage API - dual_index/shard.py

| Class / Method | Signature | Behavior |
|---|---|---|
| ShardStore | ShardStore(base_dir, num_shards) | Creates a file-backed store under base_dir, split across num_shards shards. Each shard is persisted as a JSON file `shard_{idx}.json` under base_dir containing a dict of username→record (for example `shard_0.json`). |
| put | put(username, record) | Saves a record for the username, picking the shard deterministically via a stable hash (stable across processes, not Python's per-run salted hash). Must preserve the dict exactly as given, including extra keys, without re-sorting or dropping fields |
| get | get(username) | Returns the record dict for the username or None if not present, preserving the dict exactly as stored |
| shards | shards() | Returns num_shards |

Index API - dual_index/store.py

| Class / Method | Signature | Behavior |
|---|---|---|
| DualIndex | DualIndex(base_dir, num_shards, fmt) | Wraps ShardStore and encoding; fmt is "single" or "dual". Both write_single and write_dual work regardless of fmt - during the migration a `single` store still accepts dual writes and vice versa. The `fmt` is just metadata, it doesn't block anything. |
| write_single | write_single(username, uid) | Encodes with encode_single and stores via the shard store |
| write_dual | write_dual(username, ig_uid, threads_uid, link_state) | Encodes with encode_dual and stores via the shard store |
| read | read(username) | Retrieves from the shard store and decodes, returning the normalized dict or None |

CLI - dual_index/cli.py invoked as python -m dual_index.cli

| Command | Options | Behavior |
|---|---|---|
| init | --shards N --format single\|dual --base-dir PATH (default ./data) | Creates the shard directory and records shards and format in `metadata.json` under base_dir (JSON object with keys `shards` and `format`). Must be idempotent — second init on same base_dir must not clobber existing shard files or metadata |
| write | --user NAME --uid ID --base-dir PATH for single; --user NAME --ig-uid A --threads-uid B --link-state STATE --base-dir PATH for dual (threads-uid optional for unlinked) | Stores a record using the appropriate encoding |
| read | --user NAME --base-dir PATH --output json | Prints the decoded record as JSON to stdout, or null if absent |

The command-line tool should accept standard --option style arguments, support --help to show usage, and handle options before any positional arguments. You can implement it with any Python argument parsing approach. Keep everything deterministic and offline - no network calls.

You will know it is working when python -m dual_index.cli init --shards 16 --format single --base-dir ./data then python -m dual_index.cli write --user alice --uid 1001 --base-dir ./data then python -m dual_index.cli read --user alice --base-dir ./data --output json prints a JSON object with username alice and uid 1001.

Hidden tests for this step only check this foundation - they will import dual_index.encoding, dual_index.shard, and dual_index.store and exercise the command-line tool via its own commands, not via hand-written JSON files.
