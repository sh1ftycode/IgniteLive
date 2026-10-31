"""Ignite backend CLI.

    ignite-server add-user NAME      # prints a device token once
    ignite-server serve [--host 127.0.0.1] [--port 8787]

Environment:
    IGNITE_DATA         data directory (default ~/.local/share/ignite-server)
    IGNITE_SECRET       Fernet key for encrypting stream keys (default: generated file)
    IGNITE_YOUTUBE_KEY  YouTube Data API key, enables "song - artist" search
    (The old SHIFTYTOOL_* names still work.)

Run behind HTTPS (e.g. Caddy) whenever it listens on anything but localhost.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from aiohttp import web

from ignite_live.music.youtube import YouTubeClient
from ignite_live.server.app import create_app
from ignite_live.server.store import Store, load_or_create_secret


def env(name: str) -> str:
    """$IGNITE_<name>, or $SHIFTYTOOL_<name> from before the rename to Ignite."""
    return os.environ.get(f"IGNITE_{name}") or os.environ.get(f"SHIFTYTOOL_{name}", "")


def _renamed(new: Path, old: Path) -> Path:
    """``old`` (from before the rename to Ignite) moved to ``new``; ``old`` itself if that fails."""
    if new.exists() or not old.exists():
        return new
    try:
        old.rename(new)
        return new
    except OSError:
        return old


def _data_dir() -> Path:
    share = Path.home() / ".local" / "share"
    return Path(env("DATA") or _renamed(share / "ignite-server", share / "shiftytool-server"))


def _store() -> Store:
    data = _data_dir()
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = data / "ignite.sqlite3"
    if not database.exists():
        for suffix in ("-wal", "-shm"):  # SQLite's side files, left by an unclean shutdown, go first.
            _renamed(data / f"ignite.sqlite3{suffix}", data / f"shiftytool.sqlite3{suffix}")
        database = _renamed(database, data / "shiftytool.sqlite3")
    return Store(database, load_or_create_secret(data, env("SECRET")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ignite-server")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    for name in ("add-user", "rotate-token", "remove-user"):
        commands.add_parser(name).add_argument("name")
    commands.add_parser("list-users")
    args = parser.parse_args(argv)

    store = _store()
    try:
        return _run(args, store)
    finally:
        store.close()


def _run(args: argparse.Namespace, store: Store) -> int:
    if args.command == "add-user":
        print(store.add_user(args.name))
        print("Paste this device token into Ignite. It will not be shown again.", file=sys.stderr)
    elif args.command == "rotate-token":
        print(store.rotate_token(args.name))
    elif args.command == "remove-user":
        print("removed" if store.remove_user(args.name) else "no such user")
    elif args.command == "list-users":
        print("\n".join(store.users()))
    else:
        youtube = YouTubeClient(env("YOUTUBE_KEY"))
        if not env("YOUTUBE_KEY"):
            print("IGNITE_YOUTUBE_KEY not set: song search uses YouTube's keyless search.", file=sys.stderr)
        web.run_app(create_app(store, youtube), host=args.host, port=args.port, access_log=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
