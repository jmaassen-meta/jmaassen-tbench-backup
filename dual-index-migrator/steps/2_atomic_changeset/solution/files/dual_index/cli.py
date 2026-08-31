# dual-index-migrator - deterministic offline migration
import json
from pathlib import Path
import click

from .encoding import encode_single, encode_dual, decode
from .shard import ShardStore
from .atomic import AtomicIndex

METADATA_NAME = "metadata.json"

def _get_shards(base_dir):
    p = Path(base_dir) / METADATA_NAME
    if p.exists():
        try:
            with open(p, "r") as f:
                data = json.load(f)
                shards = int(data.get("shards", 16))
                if shards > 0:
                    return shards
        except Exception:
            pass
    return 16

def _load_store_single(base_dir):
    shards = _get_shards(base_dir)
    return ShardStore(base_dir, shards)

def _load_atomic(base_dir):
    shards = _get_shards(base_dir)
    return AtomicIndex(base_dir, shards)

@click.group()
def cli():
    pass

@cli.command("init")
@click.option("--shards", type=int, required=True, help="Number of shards")
@click.option("--format", "fmt", type=click.Choice(["single", "dual"]), required=True, help="Store format")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory for shards")
def init_cmd(shards, fmt, base_dir):
    if shards <= 0:
        raise click.BadParameter("shards must be >0")
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    # per-universe dirs for Step2
    (p / "ig").mkdir(parents=True, exist_ok=True)
    (p / "threads").mkdir(parents=True, exist_ok=True)
    # also keep legacy single store dir for Step1 compat? Already base_dir
    # create empty shard files lazily
    metadata = {"shards": shards, "format": fmt}
    with open(p / METADATA_NAME, "w") as f:
        json.dump(metadata, f, sort_keys=True)
    click.echo(f"initialized {shards} shards format={fmt} at {base_dir} (ig/threads)")

@cli.command("write")
@click.option("--user", "username", required=True, help="Username")
@click.option("--uid", type=int, default=None, help="UID for single format")
@click.option("--ig-uid", type=int, default=None, help="IG UID for dual format")
@click.option("--threads-uid", type=int, default=None, help="Threads UID for dual format")
@click.option("--link-state", type=click.Choice(["linked", "unlinked"]), default=None, help="Link state for dual")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
def write_cmd(username, uid, ig_uid, threads_uid, link_state, base_dir):
    # Prefer atomic per-universe stores if ig dir exists
    shards = _get_shards(base_dir)
    ig_dir = Path(base_dir) / "ig"
    threads_dir = Path(base_dir) / "threads"
    # Check if per-universe dirs exist (Step2) - if so use them
    if ig_dir.exists() and threads_dir.exists():
        # Use AtomicIndex logic via direct shard stores for simple write
        ig_store = ShardStore(str(ig_dir), shards)
        threads_store = ShardStore(str(threads_dir), shards)
        if ig_uid is not None or link_state is not None:
            if ig_uid is None or link_state is None:
                raise click.UsageError("--ig-uid and --link-state required for dual write")
            if link_state == "linked" and threads_uid is None:
                raise click.UsageError("--threads-uid required when link-state is linked")
            try:
                rec = encode_dual(username, ig_uid, threads_uid, link_state)
            except ValueError as e:
                raise click.BadParameter(str(e))
            # Write to both universe stores: IG holds dual, Threads holds simple
            ig_store.put(username, rec)
            if link_state == "linked":
                threads_store.put(username, {"username": username, "uid": threads_uid, "format": "single"})
            else:
                # unlinked: ensure threads entry absent
                # delete if exists
                path = threads_store._shard_path(username)
                data = threads_store._load_shard(path)
                if username in data:
                    del data[username]
                    threads_store._save_shard(path, data)
            click.echo(f"wrote dual {username}")
        else:
            if uid is None:
                raise click.UsageError("--uid required for single write")
            try:
                rec = encode_single(username, uid)
            except ValueError as e:
                raise click.BadParameter(str(e))
            ig_store.put(username, rec)
            # Ensure threads absent for single
            path = threads_store._shard_path(username)
            data = threads_store._load_shard(path)
            if username in data:
                del data[username]
                threads_store._save_shard(path, data)
            click.echo(f"wrote single {username}")
    else:
        # Fallback to legacy single store (Step1)
        store = ShardStore(base_dir, shards)
        if ig_uid is not None or link_state is not None:
            if ig_uid is None or link_state is None:
                raise click.UsageError("--ig-uid and --link-state required for dual write")
            if link_state == "linked" and threads_uid is None:
                raise click.UsageError("--threads-uid required when link-state is linked")
            try:
                rec = encode_dual(username, ig_uid, threads_uid, link_state)
            except ValueError as e:
                raise click.BadParameter(str(e))
            store.put(username, rec)
            click.echo(f"wrote dual {username}")
        else:
            if uid is None:
                raise click.UsageError("--uid required for single write")
            try:
                rec = encode_single(username, uid)
            except ValueError as e:
                raise click.BadParameter(str(e))
            store.put(username, rec)
            click.echo(f"wrote single {username}")

@cli.command("read")
@click.option("--user", "username", required=True, help="Username")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
@click.option("--output", type=click.Choice(["json"]), default="json", show_default=True, help="Output format")
def read_cmd(username, base_dir, output):
    shards = _get_shards(base_dir)
    ig_dir = Path(base_dir) / "ig"
    if ig_dir.exists():
        # per-universe: read from IG store
        store = ShardStore(str(ig_dir), shards)
    else:
        store = ShardStore(base_dir, shards)
    rec = store.get(username)
    if rec is None:
        click.echo("null")
        return
    try:
        decoded = decode(rec)
    except ValueError as e:
        raise click.ClickException(str(e))
    if output == "json":
        click.echo(json.dumps(decoded, sort_keys=True))

@cli.command("link")
@click.option("--user", "username", required=True, help="Username")
@click.option("--ig-uid", type=int, required=True, help="IG UID")
@click.option("--threads-uid", type=int, required=True, help="Threads UID")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
def link_cmd(username, ig_uid, threads_uid, base_dir):
    shards = _get_shards(base_dir)
    idx = AtomicIndex(base_dir, shards)
    try:
        rec = idx.link(username, ig_uid, threads_uid)
        click.echo(json.dumps(rec, sort_keys=True))
    except ValueError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))

@cli.command("unlink")
@click.option("--user", "username", required=True, help="Username")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
def unlink_cmd(username, base_dir):
    shards = _get_shards(base_dir)
    idx = AtomicIndex(base_dir, shards)
    try:
        rec = idx.unlink(username)
        click.echo(json.dumps(rec, sort_keys=True))
    except ValueError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))

@cli.command("rename")
@click.option("--from", "from_user", required=True, help="Source username")
@click.option("--to", "to_user", required=True, help="Destination username")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
def rename_cmd(from_user, to_user, base_dir):
    shards = _get_shards(base_dir)
    idx = AtomicIndex(base_dir, shards)
    try:
        rec = idx.rename(from_user, to_user)
        # rename returns new_to_ig record or None
        if rec is not None:
            click.echo(json.dumps(rec, sort_keys=True))
        else:
            click.echo("null")
    except ValueError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))

if __name__ == "__main__":
    cli()
