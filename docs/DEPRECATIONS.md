# Deprecations and legacy code

Code that is only there for something older is marked in the source so it can be found and removed:

- `# v1 LEGACY` – kept so people upgrading from before the rename to Ignite (1.2) don't lose anything.
- `# DEPRECATED` – replaced; kept only for existing callers.
- `# TODO: v2 cleanup` – planned removals. There are none open today.

Search for these markers: `git grep -n "v1 LEGACY\|DEPRECATED"`.

## Removed

| What | Version | Why |
|---|---|---|
| OBS performance metrics (`HealthMonitor`, `ObsStats`, `ObsClient.stats()`), the `obs_warn_dropped_pct` setting | next after 1.9.1 | OBS shows these itself; Ignite's copy added polling and noise. |
| The Setup Guide as a separate window (`QDialog`) | next after 1.9.1 | It is a page in the main window. |

## Still kept, and when it can go

| Marker location | What it covers | Can go when |
|---|---|---|
| `core/settings.py` `apply()` | `tts_blocked_words` renamed to `blocked_words`; theme names `shiftytool` and `ignite` renamed to `dark` | Nobody has a settings file from before 2026-10-29. |
| `core/settings.py` `default_path()` | The `ShiftyTool` folder renamed to `Ignite` (1.2) | Nobody upgrades from before 1.2. |
| `core/keychain.py` `LEGACY_SERVICE` | Secrets saved under the old name, copied once | Same. |
| `core/profiles.py` `LEGACY_FORMAT` | Profile files made before the rename | Same. |
| `server/__main__.py` `env()` | `SHIFTYTOOL_*` environment variables | Servers have moved to `IGNITE_*`. |
| `live/presence.py` `PresenceConfig.app` | Replaced by `app_for(server_default)` | Nothing else reads `.app`. |

## Decided to keep

The OBS control code (`integrations/obs.py`, `obs_studio.py`, `obs_targets.py`) is **not** deprecated:
scenes, recording, clips, output tuning, the panic scene, adding overlays to OBS and the Aitum vertical canvas
all use it.
