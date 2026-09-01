# dual-index-migrator - deterministic offline migration v4.3
# step 1 - file-backed contract
# step 1 - deterministic
import json
from pathlib import Path
import click

from .encoding import encode_single, encode_dual, decode
from .shard import ShardStore

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


def _load_store(base_dir):
    shards = _get_shards(base_dir)
    return ShardStore(base_dir, shards)


@click.group()
def cli():
    pass


@cli.command("init")
@click.option("--shards", type=int, required=True, help="Number of shards")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["single", "dual"]),
    required=True,
    help="Store format",
)
@click.option(
    "--base-dir", default="./data", show_default=True, help="Base directory for shards"
)
def init_cmd(shards, fmt, base_dir):
    if shards <= 0:
        raise click.BadParameter("shards must be >0")
    p = Path(base_dir)
    p.mkdir(parents=True, exist_ok=True)
    # create empty shard files
    store = ShardStore(base_dir, shards)
    # touch shards lazily; just write metadata (idempotent - do not clobber existing)
    meta_path = p / METADATA_NAME
    if not meta_path.exists():
        metadata = {"shards": shards, "format": fmt}
        with open(meta_path, "w") as f:
            json.dump(metadata, f, sort_keys=True)
    click.echo(f"initialized {shards} shards format={fmt} at {base_dir}")


@cli.command("write")
@click.option("--user", "username", required=True, help="Username")
@click.option("--uid", type=int, default=None, help="UID for single format")
@click.option("--ig-uid", type=int, default=None, help="IG UID for dual format")
@click.option(
    "--threads-uid", type=int, default=None, help="Threads UID for dual format"
)
@click.option(
    "--link-state",
    type=click.Choice(["linked", "unlinked"]),
    default=None,
    help="Link state for dual",
)
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
def write_cmd(username, uid, ig_uid, threads_uid, link_state, base_dir):
    store = _load_store(base_dir)
    # Check if threads-uid was provided: click leaves None if not provided
    # Need to distinguish 0 vs not provided; None means not provided
    if ig_uid is not None or link_state is not None:
        # dual path
        if ig_uid is None:
            raise click.UsageError("--ig-uid is required for dual write")
        if link_state is None:
            raise click.UsageError("--link-state is required for dual write")
        # For linked, threads_uid must be provided and int
        # For unlinked, threads_uid may be omitted -> None
        if link_state == "linked" and threads_uid is None:
            raise click.UsageError(
                "--threads-uid is required when link-state is linked"
            )
        if link_state == "unlinked" and threads_uid is not None:
            # Encode expects None for unlinked; but if user provides it, let encode_dual validate
            # We'll pass as provided; if they provided a value for unlinked, encode will raise
            pass
        try:
            rec = encode_dual(username, ig_uid, threads_uid, link_state)
        except ValueError as e:
            raise click.BadParameter(str(e))
        store.put(username, rec)
        click.echo(f"wrote dual {username}")
    else:
        # single path
        if uid is None:
            raise click.UsageError(
                "--uid is required for single write (or provide --ig-uid for dual)"
            )
        try:
            rec = encode_single(username, uid)
        except ValueError as e:
            raise click.BadParameter(str(e))
        store.put(username, rec)
        click.echo(f"wrote single {username}")


@cli.command("read")
@click.option("--user", "username", required=True, help="Username")
@click.option("--base-dir", default="./data", show_default=True, help="Base directory")
@click.option(
    "--output",
    type=click.Choice(["json"]),
    default="json",
    show_default=True,
    help="Output format",
)
def read_cmd(username, base_dir, output):
    store = _load_store(base_dir)
    rec = store.get(username)
    if rec is None:
        click.echo("null")
        return
    try:
        decoded = decode(rec)
    except ValueError as e:
        # If decode fails, just output raw?
        raise click.ClickException(str(e))
    if output == "json":
        click.echo(json.dumps(decoded, sort_keys=True))
    else:
        click.echo(json.dumps(decoded, sort_keys=True))


if __name__ == "__main__":
    cli()

# step - file-backed contract - deterministic
