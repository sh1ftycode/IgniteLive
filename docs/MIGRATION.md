# Moving to the next version of Ignite

**Your settings, profiles, viewers, sounds and reports carry over by themselves.** Ignite reads
`settings.json` leniently: a setting it no longer has is ignored, and a setting it doesn't find gets its
default. There is nothing to convert and no step to run.

Your data lives in the Ignite folder (`%APPDATA%\Ignite` on Windows, `~/Library/Application Support/Ignite`
on macOS, `~/.config/ignite` on Linux). Copy it somewhere first if you like. Ignite only adds an `images`
folder (see below).

## What is different

| Before | Now | What you need to do |
|---|---|---|
| Setup Guide opened in its own window | It is a page in the main window (Help → Setup Guide, or on first launch). Finish Later returns to the page you were on. | Nothing. |
| Overlays opened in your browser to preview | **Preview with Sample Data** runs the overlay inside Ignite. **Open in Browser** is still there. | Nothing. |
| OBS performance numbers (bitrate, dropped frames, FPS, CPU, memory) and the "Warn when dropping" setting | Removed. OBS control (scenes, recording, clips, tuning, panic) is unchanged. | If you relied on the warnings, watch OBS's own Stats dock. The saved `obs_warn_dropped_pct` value is ignored. |
| Low-disk and frame-skip items in the pre-stream checklist | Removed with the numbers above. | Nothing. |
| "Read TikTok chat through the server" in Preferences → Server, and "Change title and category while live" in Preferences → Advanced | Both are in **Preferences → Experimental**, off unless you turn them on. | Your saved choice is kept; look in the new tab if you want to change it. |
| Remote control was a checkbox even when signed in | Signed in to an Ignite account, remote control is on and the checkbox is locked. Signing out unlocks it. | If you don't want it, sign out of the server. |
| Discord Rich Presence Application ID in the main form | Under **Advanced**. Empty means the application your Ignite server gives out, then Ignite's own. | Nothing. A value you set still wins. |
| Profile pictures downloaded again every session | Kept in the `images` folder for a week and used when the network can't give a new one (50 MB at most). | Nothing. Delete the folder to clear it. |
| Hosted overlay links failed silently when the server was down | Ignite checks the server every 15 seconds; after two missed answers it tells you once, and the API tab's hosted links use this PC until the server is back. | Nothing. |

## Servers you run yourself

- `GET /v1/config` is new (public, no token). It returns `{"discord_app_id": "…"}`. Set the application
  with `IGNITE_DISCORD_APP_ID`; leave it unset and apps use their built-in one. An older server without
  this route works too: apps treat it as "nothing set".
- `GET /v1/health` is what apps now call every 15 seconds. It already existed.

## Going back

Older versions ignore the new `images` folder and the settings they don't know, so settings saved by this
version open fine in the previous one. The Experimental options keep their old setting names.
