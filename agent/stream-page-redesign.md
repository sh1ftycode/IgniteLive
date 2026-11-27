# 1.9.0 redesign — handoff

Part of the 1.9.0 "Modern Streaming Control Center" UI/UX redesign. **No commits, pushes, or releases
have been made for any of this — that constraint stands until the user says otherwise.** No "pro"
features, and only real data / working controls (never fake states).

## LATEST: full desktop rewrite to the "IgniteLive — Complete UI Implementation Prompt" spec

The user then asked for the old UI to be thrown away and rebuilt to a pasted spec ("follow this to the
tea, nothing the same, picture as inspo"). **Both the desktop app and the web dashboard are now rebuilt**
to this spec (the web round happened in a later session, from the worktree
`.claude/worktrees/stream-page-redesign-a27711`, branch `claude/stream-page-redesign-a27711` — a fresh
worktree, so that session first had to `cp -r` the desktop's uncommitted `src/`/`android/` changes and
the untracked `web/`/`tests/` folders in from the main checkout before it could build on any of this;
see "Resuming this in a new worktree" below if that's you too).

### State
- Desktop: done, tested, light + dark checked. All changes are uncommitted in the main checkout.
  `tests/` and `web/` are not tracked by git.
- Web: **done** (this session) — see "Web dashboard redesign" below. Also uncommitted, also synced into
  the main checkout's `web/` (its only copy outside a worktree, same as the desktop round).

### Information architecture (exactly 8 sidebar items)
Overview, Stream Control, Live Chat, Alerts, Analytics, Overlays, Integrations, Settings
(page keys `overview stream chat actions streams overlays api settings`). Folded in:
- Music + Viewers → **Live Chat** tabs (Chat / Viewers & Points / Song Requests): `pages["chat"].music/.viewers`
- Goals + Chat Games → **Overlays** tabs
- Preferences dialog → **Settings** page (embedded `PreferencesDialog` content, Save/Discard bar)
- Old links still work via `MOVED` in `main_window.py` (`music`, `viewers`, `games`, `goals`, `obs`);
  `open_page("prefs-<Section>")` opens a Settings section; `"actions:activity"` opens an Alerts tab.
- Gone: menu bar, toolbar, docks, status bar. Replaced by header (page title/description, connection +
  stream status pills, Panic, notification bell, profile menu), toasts + notification centre, and
  shortcuts registered on the window (all `AppActions` + Ctrl+1…8, F1).

### New / rewritten files (`src/ignite_live/ui/`)
- `lucide.py` (99 Lucide icons + aliases for old glyph names), `kit.py` (component library: Card,
  MetricCard, LineChart, Legend, Pill, StatusIndicator, EmptyState, Banner, EventRow, ToastHost,
  PageContainer, button helpers), `cards.py` (shim to kit), `sidebar.py`, `shell.py` (AppHeader,
  NotificationCenter, BellButton, ProfileButton), `main_window.py`.
- Pages: `overview.py`, `live_chat.py`, `settings.py` (new); `stream.py`, `actions.py` (Alerts),
  `streams.py` (Analytics), `overlays.py`, `api.py` (Integrations, class still `ApiPage`) rewritten.
- `core/palettes.py`: spec tokens (bg #0B0D12, sidebar #10131B, panel #151923, raised #1B202C,
  border #282E3A, accent #A78BFA, primary #7C3AED…), new `sidebar/primary*/on_primary` fields (exported
  to web CSS variables automatically). `theme.py` stylesheet is token-based.
- Backend additions: `controller.grab_preview(on_image, width, source)` + `integrations/obs.py:preview`
  (live OBS frame for Overview), `controller.send_tiktok_chat(text)` (composer; needs server sign-in).

### Gotchas learned
- **Never name a widget attribute after a Qt method** (`self.metric`, `self.size`, `self.actions` on a
  widget): Qt calls back into the Python override and crashes inside `apply_theme` (this caused a hard
  access-violation when switching dark/light). Use `trend_metric`, `text_size`, `button_row`.
- Removed layout widgets need `.hide()` before `.deleteLater()` or they linger on screen.
- `&` in tab text is a mnemonic: write `"Viewers && Points"`.
- Offscreen Qt screenshots need `QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\segoeui.ttf")`.

### Verification (desktop)
- `QT_QPA_PLATFORM=offscreen python -m unittest discover tests`: 454 tests, no redesign regressions.
  Remaining failures are environmental: ~70 Windows `viewers.sqlite3` temp-folder PermissionErrors
  (also present on base `af29ba3`), `IgniteLiveAPI` not installed (test_chat_feed/tiktok_chat/tls),
  and `test_single_instance` (unrelated, file untouched). Tests updated for the new layout:
  `tests/test_app.py`, `tests/test_moderation.py`.
- Screenshots of all 8 screens, dark and light, looked right (fresh profile, nothing connected).

### Web dashboard redesign (this session — responsive, not a copy of desktop; PC-only features, like the
Integrations page's API keys, stay desktop-only)

- **New shell**, replacing the old toolbar/mobile-switch/status-bar entirely (`web/ignite_web/pages.py`):
  - `_sidebar()`: brand row (flame + "IgniteLive" wordmark), the same 11 section tabs as before (now with
    real Lucide icons, see below), a footer account row (avatar initial + username) + version + the
    existing collapse toggle. Active row is now a solid `--primary`-filled pill (was a faint `--selected`
    tint), matching the desktop sidebar's look.
  - `_app_header()`: page title + subtitle (from the new `PAGE_SUBTITLES` dict, matching desktop's
    `SCREENS` descriptions), a chat-connection pill and an OBS/stream pill (both clickable — the first
    opens Integrations, the second Stream Control), Panic, a bell with a popover notification list +
    unread badge, and a profile button opening a popover with the Mode select, the old topbar's nav links
    (Stream Key/Connect a PC/Account/Feedback) and Log Out. `page()` gained a `shell=True` flag that skips
    the generic `<header class="topbar">` entirely for the dashboard route (the sidebar + header above
    replace it); every other page is unchanged.
  - `_bottom_nav()`: phones only — Home/Chat/Alerts/Analytics as direct buttons, a More button opening a
    bottom sheet (`#more-sheet`) listing everything else (Overlays, Integrations, Settings, Music,
    Viewers, Games). Every one of these is just a `data-goto` proxy that clicks the real sidebar tab
    button — there's still exactly one tab system, no separate panel switcher.
  - Status bar (`#message`/`#readout`/`#dots`) is **gone**: `say()` now shows a toast (bottom-right,
    `#toast-host`, matching desktop's `ToastHost` — errors stay 12s and are dismissible, others 6s), and
    the live `#readout`/`#dots` were dropped as pure duplicates of info already on the Stream Control page
    (the OBS health card, the Connections table) — nothing was lost.
- **Lucide icons**: `pages.py`'s old hand-drawn 16x16 `NAV_ICONS`/`SECTION_ICONS` were replaced with the
  *exact* Lucide path data from `src/ignite_live/ui/lucide.py` (24x24 viewBox, stroke-width 2) — true
  icon parity with desktop, not just a similar style, which is what `lucide.py`'s own docstring already
  promised ("The web dashboard uses the same paths … so both look alike") but hadn't done yet.
- **Live Chat is now a real page**: `_chat_page()` holds the actual `<ol class="feed" id="feed">` (moved
  out of the old always-visible `.dock.chat`), plus the header's "Mute Speech" button (desktop keeps that
  control on its own Live Chat page too, via `ui/pages/live_chat.py`). The old Activity dock is gone the
  same way the notification centre replaced it on desktop.
- **Toolbar buttons relocated** (the toolbar itself is gone): `#record`/`#clip` (Start/Stop Recording,
  Save Clip) moved into Stream Control's "Recording and clips" card, next to the controls already there;
  `#mute` (Mute Speech) moved into the new Live Chat page; the toolbar's own Start/Stop Streaming and Skip
  Song were dropped as straight duplicates of Overview's `#ov-stream` and Music's existing Skip Song
  button. No functionality lost, same ids, so `dashboard.js`'s existing enable/disable/label logic needed
  no changes for any of these.
- Every page's redundant `<h1>` was dropped from `.page-head` (the title is now only in the app header);
  a few pages also had an intro `<p>` that was a near-duplicate of the new header subtitle and got
  trimmed (Overview, Analytics). Chips (`#live-chip`, `#game-chip`) and the Mute Speech button kept their
  `.page-head` row.

### Gotcha learned (web)
**Don't reuse an existing CSS class name on a differently-laid-out element just to hook a `querySelector`
on it.** The new profile popover's nav needed `ignite.js`'s Android-app "Server" link injector
(`document.querySelector(".who")`) to find it, so it was given `class="profile-links who"` — but `.who`'s
*own* rules (`display:flex; align-items:center; margin-left:auto;`, meant for the old one-row topbar
corner) fought the new `.profile-links{flex-direction:column}` rule and squashed the whole menu into a
narrow, centered, right-shoved column (looked like right-aligned text at a glance; it wasn't — the
container itself had shrunk and been pushed to the edge). Fixed by giving `ignite.js` a neutral
`[data-android-host]` attribute hook instead of reusing `.who`, which is purely a look, not a behaviour
contract.

### Verification (web)
- `cd web && python -m unittest discover tests`: 61 tests, same 2 pre-existing `test_desktop_hosted`
  sqlite-cleanup errors as the untouched baseline — no regressions. (Run with `PYTHONPATH` pointing at
  *this* worktree's `src/`, not whatever `pip install -e` points at — a fresh worktree's editable install
  still targets the worktree it was installed from.)
- Real browser (the Claude Code Browser pane, Chromium), a scratch `aiohttp` server + demo account, both
  themes, both widths: desktop (800px) — sidebar, collapse, header pills, bell popover, profile popover
  (links, Mode select, Log Out), Overview, Stream Control (new Recording buttons), Live Chat (real feed +
  Mute Speech), a toast. Phone (375px) — bottom nav incl. active-state sync, the More sheet (open, pick an
  item, closes and navigates), the profile popover as a bottom sheet. Light mode checked on both widths.
- **Known trap if you re-verify visually**: same as the desktop/earlier-web rounds — the scratch preview
  server imports `ignite_web.pages` once at startup; editing `pages.py` needs a process restart (kill +
  relaunch) to pick it up. `static/*.js`/`.css` are served live, no restart needed for those.

### Next
1. Optional polish: Settings section heading sits slightly tight to the top (desktop-only, not web).
2. Confirm with the user before widening scope, same as every prior round. Nothing is committed anywhere.

### Resuming this in a new worktree
Same situation as the desktop round, just for both surfaces now: `web/` and `tests/` are **not tracked**
by this repo's git (`.gitignore`/`.git/info/exclude` excludes both; `web/` is its own separate git repo
with its own history, sitting untracked inside this project's folder) and only exist, uncommitted, in the
**main checkout** (`C:\Users\Admin\Downloads\Dev Shi\NotShiftyAtALL`). A fresh worktree starts without
either. Before building on this further in a new worktree: `cp -r` both folders in from the main checkout,
and also copy in the desktop's modified/new `src/`/`android/` files listed in `git status` there (nothing
from either redesign round has been committed anywhere, so the main checkout's working tree is the only
complete copy outside the worktree each round was done in).

---

# Older notes (superseded for desktop; the web sections below still describe the live web state)

Part of the 1.9.0 "Modern Streaming Control Center" UI/UX redesign (dark purple theme,
Linear/OBS Studio/Vercel-inspired, no generic-AI-dashboard clichés). This note now covers the
Stream page AND the Overview page + full nav restructure. **No commits, pushes, or releases
have been made for any of this — that constraint stands until the user says otherwise.**

## Phases so far (for context)

1. Design system / palette — done.
2. Desktop sidebar navigation — done (`src/ignite_live/ui/sidebar.py`).
3. Web dashboard sidebar — done (`web/ignite_web/pages.py` nav, `web/ignite_web/static/ignite.css`).
4. Stream page redesign, both surfaces — done (see below).
5. Overview page + full nav restructure (match the reference picture exactly), both surfaces — done
   (see below). This is the session that renamed/reorganized the sidebar itself, not just a page.
6. Mobile (Phase 4 in the original numbering) — **not started**. Open.

## IMPORTANT: where this work actually lives

`web/` and `tests/` are **not tracked by this repo's git** (`.gitignore` excludes both; `web/` is
in fact its own separate git repository with its own history, sitting untracked inside this
project's folder). They only exist in the **main checkout**
(`C:\Users\Admin\Downloads\Dev Shi\NotShiftyAtALL`), not in any fresh worktree. If you're resuming
this in a new worktree, you must `cp -r` both folders in from the main checkout before you can
build on this (see git status there for exactly what's uncommitted in `src/`/`android/` too — as
of this note, nothing from this redesign has been committed anywhere, so the main checkout's
working tree is the only copy outside this worktree).

## What's done: Overview page + full nav restructure, both surfaces

The user asked to make the app match a reference picture of an "Overview" dashboard **entirely**,
on both desktop and web ("both, matched", same as the Stream page round). The picture's sidebar
(Overview, Stream Control, Live Chat, Alerts, Analytics, Overlays, Integrations, Settings) didn't
match the app's actual pages (Stream, Music, Overlays, API, Actions, Streams, Viewers, Games), so
per the user's explicit choice: **the nav was renamed/reordered to match the picture exactly**,
folding old pages into the closest new section, with whatever didn't fit (Music, Viewers, Games)
kept fully working but moved out of the primary curated set rather than deleted.

### Nav mapping (same keys both surfaces, just relabeled/reordered)

| key | old label | new label | icon |
|---|---|---|---|
| `overview` | *(new)* | Overview | `home` |
| `stream` | Stream | Stream Control | `broadcast` (unchanged) |
| `chat` | *(dock only)* | Live Chat | `chat` |
| `actions` | Actions | Alerts | `bell` (was `zap`; `zap` now means Quick Actions only) |
| `streams` | Streams | Analytics | `chart` (unchanged — was already a bar-chart glyph) |
| `overlays` | Overlays | Overlays | `layers` (unchanged) |
| `api` | API | Integrations | `puzzle` (was `connect`) |
| `settings` | *(menu only)* | Settings | `gear` |
| `music`/`viewers`/`games` | — | — | unchanged, just lower in the list / hidden by default |

### Desktop (`src/ignite_live/ui/main_window.py`, `src/ignite_live/ui/pages/overview.py`)

- `PAGE_TITLES`/`PAGE_ICONS` reordered + relabeled per the table above. New installs get
  `DEFAULT_HIDDEN = ("music", "viewers", "games")` applied to `settings.closed_tabs` on first run
  only (`_restore_sidebar()`: `if not s.tab_order and not s.closed_tabs: ...`) — existing users'
  saved layouts are untouched.
- `SIDEBAR_EXTRA` + `FIXED_SIDEBAR_KEYS` (`chat`, `settings`): always in the sidebar, in fixed
  spots (`_with_fixed()` puts Live Chat right after Stream Control, Settings always last), not
  reorderable/hideable, and **not** part of `PAGE_TITLES`/`self.pages`/`content_stack` — they
  reuse `open_page()`'s existing special-cased routing (`key=="chat"` raises the chat dock,
  `key=="settings"` opens Preferences) that was already there before this change, just never
  exposed as sidebar buttons until now. `_sidebar_content_keys()`/`_set_sidebar_content()` are the
  new seam between "content pages the user can hide/reorder" and "the full sidebar list".
- `self.chat_panel = ChatPanel(...)` construction moved earlier (before `self.pages` is built) so
  `OverviewPage` can take it as a constructor arg and seed its own embedded `ChatFeed` from
  `chat_panel.history`.
- `OverviewPage` (new file): stat row (Viewers/Stream Uptime/Followers/Subscribers via
  `cards.StatTile`), Stream Preview (static placeholder + real live/offline state — there's no
  actual video-preview capability in this codebase, so this is honest about that), Stream Control
  mini-card (reuses `actions.toggle_stream`/`actions.end_live` — the same `QAction`s the full
  Stream Control page uses, not a duplicate flow), a second `ChatFeed(compact=True)` instance for
  Live Chat (confirmed via research that `ChatFeed` is a standalone widget, not dock-owned, so a
  second instance is safe), a hand-painted `_WeekChart` (7-day Views/New-followers line chart from
  `controller.saved_streams()` + `live/analytics.py`'s `load()`/`summary()` — real aggregation, no
  invented data), Recent Events (real `ChatFollow`/`ChatGift`/`ChatShare` from `chat_event`, newest
  4), Quick Actions (`open_page_requested` signal → `MainWindow.open_page`).
- **Honesty notes** (don't "fix" these to match the picture's literal mockup numbers):
  - Subscribers tile = count of `controller.audience_snapshot` entries where
    `user.is_subscriber` — a live snapshot, not a fabricated running total (no such stat exists
    anywhere in this codebase).
  - Viewers/Followers tiles show "–" (not "0") when nothing's been measured yet, via the existing
    `live.audience.compact()` convention already used by the Stream Control page's own tiles —
    consistent with the rest of the app, not a bug.
  - No fake "Platform" dropdown with options that don't exist; the Stream Control mini-card reuses
    the real OBS output-target combobox (same wiring as the full Stream Control page).
- New icon glyphs in `ui/icons.py`: `home`, `bell`, `puzzle`, `clock`, `star`. Rendered and visually
  checked (offscreen Qt, see Verification) — all clean line glyphs, no artifacts.

### Web (`web/ignite_web/pages.py`, `static/ignite.css`, `static/dashboard.js`, `static/dashboard-tabs.js`)

- `DASHBOARD_TABS` reordered: the curated 8 first (matching the table above), then
  `music`/`viewers`/`games` still present and fully working, just after Settings. Nothing was
  deleted — the web dashboard never had a hide/show mechanism like desktop's, so "moved lower"
  was the honest equivalent of desktop's "hidden by default but a click away".
  Overview is now index 0 (the default/first tab); `_stream_page()`'s wrapping div picked up
  `hidden` since it's no longer the default.
- New page builders: `_overview_page()`, `_chat_page()`, `_integrations_page()`. `_chat_page()` and
  the Overview's "Live Chat" card are **intentionally informational, not a duplicate chat feed** —
  the real chat dock is always visible beside the content on wide layouts, so these just point at
  it (and, on a phone, switch the existing mobile Control/Chat/Activity switcher to "Chat") via a
  shared click handler (`dashboard.js`: `#ov-open-chat`/`#chat-page-open` → click the real
  `[data-panel="chat"]` switcher button + `scrollIntoView`). `_integrations_page()` is similarly
  just a pointer: hosted widget links and API keys are deliberately desktop-only (they're PC-local
  secrets per `ui/pages/api.py`'s own docstring), so the web page says so rather than re-exposing
  them in a browser session.
  **Web has no real "share" events reaching the browser** (confirmed in
  `overlay/chat.py:chat_payload()` — it only ever sends `comment`/`gift`/`follow`/`join` over the
  dashboard websocket, unlike the local overlay API which also has `share`), so web's Recent Events
  only ever shows follow/gift, never share. This is a known, deliberate asymmetry with desktop
  (which does have `ChatShare` available) — not a bug to fix without also plumbing `share` through
  `chat_payload()`/the wire protocol, which is out of scope for this pass.
- `dashboard.js`: added `chatWatchers` (an array `addChat()` notifies on every real line, skipped
  during the "now mixed, redraw everything" replay pass so events aren't double-counted) and
  `window.IgniteDashboard.watchChat()` to expose it to `dashboard-tabs.js`; added the shared
  "open chat" click handler described above.
- `dashboard-tabs.js`: new `D.on(["live","studio"], ...)` for Overview's live/offline state, stream
  health and the uptime/start-stop button (reuses `data-command="toggle_stream"`, the same generic
  `[data-command]` click wiring every other command button uses — no new click handler needed for
  the button itself); new `D.on(["stats","audience"], ...)` for the 3 real stat tiles (Subscribers
  via `people.filter(p => p.subscriber)` — the dashboard's `audience_payload()` already sends a
  `subscriber` bool per person, confirmed in `overlay/api.py:user_payload()`); new
  `D.on(["reports"], ...)` that buckets `state.reports` (already real, already sent for the
  Analytics/"Streams" tab) by day into a hand-drawn inline SVG line chart — same 7-day rollup logic
  as desktop's `_WeekChart`, just in JS/SVG instead of QPainter; `recordOverviewEvent`/
  `renderOverviewEvents` via `D.watchChat()`; a generic `[data-goto]` handler for Quick Actions that
  just clicks the real sidebar tab button (`document.getElementById("tab-" + goto).click()`).
- New CSS in `ignite.css`: `.overview-row` (the 3-cards-per-row grid, reflows to 1 column under
  `minmax(240px,1fr)` — confirmed responsive down to phone width), `.preview-box`, `.week-chart`/
  `.chart-legend`, `.ov-events`, `.quick-actions`.
- New `NAV_ICONS`/`SECTION_ICONS` entries matching desktop's new glyphs (hand-translated to SVG
  path strings, not shared code with `icons.py` — the two are independent implementations by
  design, same as the existing icons).

### Verification done

- Desktop: 439 tests (`QT_QPA_PLATFORM=offscreen python -m unittest discover tests`, Windows).
  Bisected the pre-existing failures against the untouched `af29ba3` checkout to confirm they're
  **not** regressions: the Windows-only `tempfile.TemporaryDirectory` + sqlite WAL-file cleanup
  race (`PermissionError: ... viewers.sqlite3`) already fails ~70 tests on this machine before any
  redesign changes at all; `test_mobile`/`test_tiktok_chat`/`test_tls`/`test_server*` fail here for
  unrelated environment reasons (missing web icons in this worktree, `IgniteLiveAPI`/`cryptography`
  not installed by default — installed into the global Windows Python purely to run tests, nothing
  committed). Two real test-logic fixes were needed and applied in `tests/test_app.py` (not
  tracked by git, but kept in sync locally): `LayoutTests` needed to filter `PAGE_TITLES`-only keys
  out of `sidebar.visible_keys()` (which now also includes `chat`/`settings`), and one assertion
  updated from `"Stream"` to `"Stream Control"` / from landing-on-`"stream"` to landing-on-
  `"overview"` for an unknown page key (the new fallback/home page).
- Web: 61 tests (`cd web && python -m unittest discover tests`) — only the same pre-existing
  sqlite-cleanup artifact (2 tests), nothing related to this change.
- Visual, desktop: offscreen Qt `grab()`, dark + light themes. (This Windows box's offscreen Qt
  platform plugin finds **zero** system fonts by default — had to
  `QFontDatabase.addApplicationFont()` a real font file manually to get readable text in a
  screenshot; this is a one-off test-harness quirk, not a bug, and didn't block anything — layout
  was clearly correct even in the tofu-box render.)
- Visual, web: real browser (not offscreen) against a scratch `aiohttp` server signed in as a real
  account, at both desktop width (1700px) and phone width (375px, via the mobile Control/Chat/
  Activity switcher) — confirmed layout, nav order, Quick Actions navigation, and the Live Chat/
  Show Chat buttons.
- **Known trap if you re-verify visually**: same as before — the scratch preview server imports
  `ignite_web.pages` once at startup; editing `pages.py` needs a process restart (kill + relaunch)
  to pick it up. `static/*.js`/`.css` are served live, no restart needed for those.

## What's next (not started, no instruction yet to proceed)

1. **Phase 4: mobile.** Still not started. The Overview page's CSS grid already reflows cleanly to
   phone width (confirmed via screenshot this session), so this remains additive polish, not a fix
   for breakage.
2. **Web's missing `share` event.** If the user wants web's Recent Events to show shares too (to
   fully match desktop), that needs `overlay/chat.py:chat_payload()` extended to handle `ChatShare`
   and the wire protocol to carry it — a small, separate, explicit ask away from "redo the nav".
3. **Other pages.** Same restraint as before: only Overview got the full card/stat-tile/chart
   treatment. Reuse `Card`/`StatTile` (desktop) and `_card()`/`_stat_tile()` (web) rather than
   inventing new containers if this expands further.
4. **Confirm with the user before widening scope**, same as every prior round.
