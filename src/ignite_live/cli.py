"""Terminal helpers.

    ignite-streamlabs-login [--show-token]

Signs in to Streamlabs in your browser and saves the token to the system
keychain, where the Ignite app picks it up on its next start. Prints the
login link too, so it works when no browser opens by itself.
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from ignite_live.core import keychain
from ignite_live.integrations.streamlabs import ProviderError, StreamlabsExperimentalProvider, StreamlabsTokenRetriever


def _open(url: str) -> bool:
    print("\nOpening the Streamlabs login in your browser. If nothing opens, copy this link into any browser:\n")
    print(f"  {url}\n")
    print("Log in with your TikTok account there. Waiting (up to 5 minutes)…", flush=True)
    webbrowser.open(url)
    return True  # The link is printed, so a failed auto-open is fine.


def streamlabs_login(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ignite-streamlabs-login", description=__doc__.split("\n\n")[2])
    parser.add_argument(
        "--show-token", action="store_true",
        help="also print the token, e.g. to paste into Ignite on another computer (keep it secret)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="  · %(message)s")
    try:
        token = StreamlabsTokenRetriever().retrieve_token(open_url=_open)
        print("\nGot a token. Checking TikTok LIVE access…")
        info = StreamlabsExperimentalProvider(token).account_info()
    except ProviderError as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    print(f"Signed in as @{info.username} · application: {info.application_status} · can go LIVE: {'yes' if info.can_be_live else 'no'}")

    saved = keychain.save(keychain.STREAMLABS_TOKEN, token)
    if saved:
        print("Saved to your keychain. Start Ignite (or restart it) and it'll be signed in.")
    else:
        print("No system keychain found, so it wasn't saved. Paste this into Ignite → Stream → Paste a token instead:")
    if args.show_token or not saved:
        print(f"\n  {token}\n\nThis token gives full access to your Streamlabs account. Don't share it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(streamlabs_login())
