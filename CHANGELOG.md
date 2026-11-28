# Changelog

What's new in each version of Ignite. The release page for each version
starts with its section from this file (see `.github/workflows/build.yml`).
For versions before 1.6.1, see the
[GitHub releases](https://github.com/sh1ftycode/IgniteLive/releases).

## 1.9.3

### New and changed

- **Overview rebuilt to match the design:** four stat tiles (Viewers with a small trend line, Chat per min,
  Gifts, New followers), the chat beside the stream preview and go-live controls, **Scenes you can switch from
  the Overview**, recent events, and a Ctrl+K bar along the bottom. The numbers come from live data.
- **Flatter cards and buttons:** plain card titles, no divider lines, borderless buttons.
- **Chat rows follow the design:** name first, a blue Mod badge, the platform as a quiet word, neutral avatars.
- **Inter is now bundled** (SIL Open Font License), so Ignite looks the same on every PC.
- Shortcut letters (underlined) no longer show in Settings forms; the shortcuts still work. Menus keep them.
- **Setup guide rebuilt as the design's five steps**, full window: Account, Connect your channel, Pick a look,
  Add it to OBS, then Test everything and Go live. PC and OBS tuning, the OBS connection, text to speech, loyalty
  points, the panic scene and the stream key are all still there, tucked into the step they belong to.
- **Hosted links are now THE links.** Sign in and every overlay link is Ignite's, and OBS is pointed at it. If the
  server can't be reached, OBS is pointed at the same overlay on this PC (the backup) and back again when the
  server answers. Overlays and Integrations show the hosted link first with the PC link as a labelled backup.
- **Themes**, a new screen in the sidebar: one look for the dashboard and the overlays. Pick any of the 21
  built-in themes (the dashboard wears it too, unless you turn off "Match my overlays"), switch with the season
  automatically, or **make your own**: five colours, corner radius and an overlay font, with a preview of the
  overlays and of the dashboard and a check that the text can still be read. Your themes are saved, and can be
  duplicated, exported to a file and imported again. Saved with your settings, so they sync with your account.
- **Dashboard background** (Themes): a picture behind the screens, from four built-in ones or your own, with Hue,
  Blur and Dim, and Full, Subtle or Off. Panels turn slightly see-through so the picture shows, and Subtle keeps
  text easy to read. Your picture is copied into the Ignite folder (`backgrounds`) and stays on this PC. Overlays
  stay see-through: they sit on top of your scene in OBS, so a picture there would cover your stream.
- **Music plays in Ignite (optional):** "Play music in Ignite" on Live Chat → Song Requests runs the player inside
  Ignite, visible in that tab, instead of inside the Now Playing overlay in OBS. The overlay then only shows the
  cover, title and progress, and plays again if you switch this off. In OBS, add Ignite's audio (Application Audio
  Capture, or Desktop Audio). Off by default, so nothing changes until you turn it on.
- **Covers instead of videos:** a pasted music-video link is swapped for the same song's art track (the album cover)
  when YouTube Music has a clear match, and songs without cover art look one up on Apple's public iTunes search
  (only the title and artist are sent).
- Every checkbox is a toggle switch; form labels lose their trailing colons; tables have taller rows.
- The "Ignite server" card is gone from Integrations, and screens now say "your Ignite account".

### Removed

- The Overview's analytics chart and quick-action buttons. Analytics has the same reports, and the sidebar and
  Ctrl+K reach every screen.

## 1.9.2

### Added

- **A new look:** a calmer, dark-first design with one ember accent (red only for live), rounder
  cards without outlines, new stat tiles, and a matching light mode. The web dashboard follows it.
- **Command bar (Ctrl+K):** type to jump to a screen, panic, skip a song, switch an OBS scene or
  change the theme. Nothing happens until you press Enter.

### Also in this version

- **Overlay preview in the app:** Overlays → Preview with Sample Data runs the
  selected overlay inside Ignite (16:9, with sample songs and chat). Open in
  Browser still opens it in your web browser.
- **Server health check:** Ignite pings your Ignite server every 15 seconds
  (every 5 while it is down). After two missed answers it tells you once, and
  hosted overlay links use this PC until the server is back.
- **Discord application from the server:** a server can hand out the Discord
  application Rich Presence shows as (`IGNITE_DISCORD_APP_ID`, served at
  `/v1/config`). Your own Application ID, now under Advanced, still wins.

- **Account button in the header:** "Sign in" until you have an account, then
  your name, and "Server offline" when it isn't answering. The Setup Guide now
  suggests signing in first, and the API tab shows which server it uses, with
  one button to Preferences → Server.
- **Pictures kept for a week:** chat profile pictures and gift icons are saved
  in the Ignite folder (`images`, 50 MB at most) and used again at once, or when
  the network can't give a new one.
- Docs: `docs/MIGRATION.md` (what changes when you update, and that settings
  carry over by themselves) and `docs/DEPRECATIONS.md` (legacy code and when it
  can go).

### Changed

- **Setup Guide look:** one centred column with a progress bar, status tiles that turn green when
  ready, and dashed "+ Twitch" / "+ YouTube" buttons that open Preferences.
- **The Ignite server is built in and private:** there is no Server URL to type, it isn't shown
  anywhere in the app or the log, and it can't be changed from the settings. Builds without a server
  (running from source without `IGNITE_MAIN_SERVER`) simply have no account features. Ignite is a
  hosted service, so self-hosted servers on a home network and the device-token box are gone; plain
  `http://` only works to this PC, for development.
- **Preferences → Experimental:** reading TikTok chat through the Ignite server
  and changing the title while live moved here, off unless you turn them on.
- **Remote control is on while signed in** to an Ignite account, and its
  checkbox is locked until you sign out.
- **Server reads are retried** twice before Ignite says the server can't be
  reached, and every failure is logged with what was being asked.
- **Setup Guide is part of the main window** instead of a separate window.
  Finish Later returns you to the page you were on.
- **Stream preview resizes:** it fetches frames at the size it is shown, and
  can be made smaller.

### Removed

- OBS performance numbers: bitrate, dropped frames, FPS, CPU and memory, the
  "Warn when dropping" setting, and the low-disk and frame-skip checklist items.
  OBS control (scenes, recording, clips, tuning) is unchanged.

## 1.9.1

### Changed

- **TikTok chat defaults to your own connection:** signed-in Ignite now reads TikTok chat
  itself (free, direct, no key needed) instead of through the Ignite server by default.
  The server's Euler Stream key is shared across every signed-in account, so routing
  everyone's live chat through it burned that shared quota for no benefit while the PC was
  on. The Server tab in Preferences has a new "Read TikTok chat through the server instead
  of this PC" checkbox for anyone who wants it back (e.g. an unreliable home connection).
  Dashboards reading TikTok chat while the PC is off are unaffected either way.
- **Capacity-aware server fallback:** a PC reading TikTok through the Ignite server now
  checks the server's capacity over plain HTTP before ever opening the connection; when
  the server is full, it goes straight to reading TikTok itself instead of dialing in to be
  told so.
- **Fewer wasted up/down arrows:** `QAbstractSpinBox` fields (Health, Win by, Time limit,
  and similar number fields across the app) no longer show their up/down arrows as a
  detached, gapped box; they now sit flush against the field's edge, matching every other
  input.

### Fixed

- The Ignite server re-serializes TikTok chat to JSON once per batch instead of once per
  reader, so accounts with several open dashboards sharing one TikTok connection cost less.

## 1.9.0

### Changed

- **A new look:** the desktop app's menu bar, toolbar, docks and status bar are
  replaced by a sidebar (Overview, Stream Control, Live Chat, Alerts,
  Analytics, Overlays, Integrations, Settings) and a header with connection
  and stream status, Panic, notifications and a profile menu, all built from
  one shared component library and Lucide icon set.
- **Overview:** a new first screen — metrics, a live OBS preview, go-live
  controls, chat, a chart of recent streams, and recent follows/gifts/shares —
  using only numbers Ignite already tracks.
- **Settings:** Preferences now lives in the window instead of a separate
  dialog.

## 1.8.6

### Changed

- **IgniteLiveAPI:** TikTok chat now runs through IgniteLiveAPI, Ignite's own
  fork of TikTokLive. Euler Stream's OAuth login for chat replies and
  moderation is unchanged.

### Fixed

- **Duplicate joins:** viewers who joined a TikTok LIVE no longer appear twice
  in the chat feed (the synthetic join event is now skipped since the real one
  already handles it).

## 1.8.5

### Fixed

- **Flatpak updates:** "Can't pull from untrusted non-gpg verified remote".
  Ignite's Flatpak repository is now signed, the key comes with every
  .flatpak and .flatpakref, and the Update button adds it to installs made
  before (once; a system-wide install asks for your password).

## 1.8.4

### New

- **Minecraft actions (ServerTap):** rules can run console commands on your
  Minecraft server, or broadcast a message to everyone in the game, through
  the ServerTap plugin. A command can run once per gift in a combo (at most
  50). Viewers' names come through as one plain word, so they can't add
  anything to a command. Set the server up in File → Preferences →
  Minecraft (with Test Connection).
- **Add TT Interactive Rules** (Actions tab): gifts spawn mobs, items and
  events through TT Interactive Stream Games' `/ttinteractive gift`.
- **Discord Rich Presence:** your Discord profile shows that you're live,
  where, viewers and likes, the song playing and the game running, with up
  to two buttons (e.g. Watch on TikTok). Built from modules you turn on or
  off, with presets or your own two lines. Just tick it in File →
  Preferences → Discord: it shows as "Playing Ignite" (or your own Discord
  application, if you enter one).
- **More in action rules:**
  - gift rules can have a coin range ("99–498 coins") so tiers don't
    overlap, and `{each}` gives one gift's value;
  - a **First message** trigger welcomes viewers once a stream;
  - a **Wait** action puts the rule's steps in order;
  - an alert can set how long it stays on screen;
  - **Export… and Import…** save rules to a file and add them from one.

## 1.8.3

### New

- **Song requests overlay:** what happened to each request, as cards that
  stay up long enough to read: added (and where in the queue), refused (and
  why) or skipped, coloured by outcome. Add it from the Overlays tab.
  Options: how long cards stay, how many at once, newest at the bottom.

- **Feedback & requests:** Help → Feedback & Requests opens a form on the
  Ignite server's website for bugs and ideas (also linked from its home
  page). The server's admins read and sort them in the control panel.
- **`&size=` on every overlay** makes its text bigger or smaller.

### Changed

- **Discord's "went live" for TikTok** is sent when you press Start
  Streaming, not when the stream key is made. A chat that reconnects
  mid-stream no longer announces it again.
- **Chat and leaderboard overlays have bigger, sharper text in less space:**
  the text follows the Browser Source's width (about 21 px at the default
  size instead of 13), so a small source stays readable. New leaderboard
  sources are shorter (460×340).
- **Now Playing's answers to chat** queue up instead of replacing each other,
  so a busy chat doesn't lose them.

## 1.8.2

### New

- **Sign in to TikTok** on your Ignite server's website (Account → TikTok):
  scan a QR code with the TikTok app on Euler Stream's page. Your TikTok
  password never reaches Ignite, and you can sign out on the website at any
  time.
- **Song requests answered in TikTok chat**, as you: "added", refused (and
  why), skipped, what's playing. Turn it on in File → Preferences → Chat
  platforms. At most one message every 3 seconds, so TikTok doesn't treat
  your account as a spammer.
- **Moderate your TikTok LIVE from Ignite:** the Viewers tab's TikTok button
  makes a viewer a moderator (or not), mutes them (30 seconds, 5 minutes or
  the rest of the LIVE), or kicks them out, and lets them back in. It works
  for viewers who've chatted, liked, gifted or joined since Ignite started.
  The button shows once your Ignite server's Euler Stream plan includes
  moderation (Euler's Webcast Premium add-on).

## 1.8.1

### Fixed

- **TikTok chat across reconnects.** When TikTok drops the connection,
  which happens now and then during a LIVE, the comments said while it was
  down now come through once Ignite reconnects, instead of being thrown
  away. Comments already shown aren't shown twice.
- **TikTok comments in a quiet chat** reach Ignite right away through the
  Ignite server, instead of waiting for the next like or join.
- **A PC clock that runs fast** no longer makes Ignite skip TikTok comments.
- **Songs that won't play outside YouTube** ("only on YouTube", removed, or
  a player error): for a `!sr song - artist` request, Ignite now tries up
  to two other versions of the same song before skipping it.

### Removed

- **Connect YouTube Music** (the pasted cookie) is gone, and a saved cookie
  is deleted from the keychain. Song requests, autoplay and public playlist
  links work as before.

## 1.8.0

### New

- **Commands overlay.** One overlay that lists every chat command viewers
  can use: song requests, points (with their price), text to speech and
  your action rules' commands, as you've named them, plus how to play while
  a game runs. Add it from the Overlays tab like the others.
- **An Update button in the status bar.** It shows what an update is doing
  (with the download's progress) and takes you to the next step in one
  click: check, download, or restart into the new version. **What's New**
  (Help menu, or the update banner) shows the release notes in Ignite.
- **Ignite as a Flatpak** for Linux, from Ignite's own Flatpak repository: it
  updates with the Update button, or like any other Flatpak.
- **The Ignite server's home page.** The server's address now opens a page
  to download Ignite for Windows, Linux and Android (or add it to an
  iPhone's Home Screen). The control panel moved to `/control/`; old links
  and bookmarks still lead there.

### Changed

- **Everything goes through the Ignite server.** Ignite checks for updates
  there first (GitHub only if the server doesn't answer), the Android app
  opens `/control/app`, and the Flatpak updates from the server's
  `/flatpak/`. The files themselves still come from GitHub, checked against
  each release's checksums.

- **Every copy of Ignite updates itself.** The Windows zip and the Linux
  tar.gz now download, check and install updates like the installer and the
  AppImage do, instead of sending you to the download page.

## 1.7.0

### New

- **Sign in to Ignite.** File → Preferences → Server → **Sign In…** opens
  the Ignite website with a short code: log in there with a password or
  Discord, check the code matches, and approve. No more copying a device
  token. Each signed-in PC is listed on the website under Account, where you
  can sign it out; **Sign Out** in Ignite signs it out there too.
- **TikTok chat through the Ignite server.** Once you're signed in, Ignite
  gets TikTok chat, gifts, likes, follows, shares, joins and viewer counts
  from the Ignite server (through Euler Stream) instead of running its own
  TikTok connection. If the server can't take it right now, Ignite connects
  to TikTok itself as before.
- **More of Ignite in the web dashboard.** With remote control on, the Ignite
  website's dashboard now has Viewers, Actions, Games, Overlays, Streams and
  Settings next to Stream and Music: turn rules on and off, start games,
  change the overlay theme, see your last streams, and change song request,
  text to speech, OBS, moderation and panic settings from any browser. On a
  phone it shows one panel at a time.
- **Ignite on your phone.** An Android app (`Ignite-<version>.apk` on the
  release page) and, on iPhone, the dashboard on your Home Screen (Safari →
  Share → Add to Home Screen): a remote for Ignite on your PC, through the
  Ignite server. It opens full screen, starts fast, and says so when your
  phone is offline.

### Faster

- **Busy LIVEs don't make chat fall behind any more.** Each new chat line
  made the chat panel measure every line again, so the more chat there was,
  the slower Ignite got: about 90 events a second at most, and a big LIVE
  sends more than that in likes and joins alone, so chat ran further and
  further behind. Lines are now measured once and the panel scrolls once per
  batch: about 5,600 to 8,600 events a second with the main window, the
  overlays and the leaderboards running. The leaderboards also skip
  re-sorting when an event can't change them, and the overlays and the
  server send each message to all their pages without re-encoding it for
  each.

### Fixed

- **Running Ignite from source on Python 3.15 works.** Anything it did in
  the background (saving viewer stats, song search, OBS, the server) failed
  with "'lazy_import' object is not callable".

## 1.6.5

### Changed

- **Ignite always uses the main Ignite server.** Hosted links, remote
  control and settings sync now always go through the official Ignite
  server, and Preferences → Server shows its address read-only. If you had
  set a different server, Ignite switches over on its next start: turn
  Hosted links on again (API tab) to get your new links, then paste them
  into OBS or your streaming app in place of the old ones.

## 1.6.4

### Fixed

- **Streamlabs sign-in opens your browser on Linux again.** On some Linux
  systems (for example Bazzite or Fedora with GNOME, where Firefox is a
  Flatpak), clicking Sign In did nothing: the AppImage and tar.gz builds
  passed their own bundled libraries to the system's link opener, which
  crashed before reaching a browser. Ignite now starts the browser with your
  system's own settings. If no browser opens anyway, it shows the sign-in
  link with a Copy Link button, and you can paste it into any browser on the
  same computer. The other buttons that open links or folders work again too.

## 1.6.3

### Fixed

- **Ignite servers behind Cloudflare work again.** Ignite's requests to an
  Ignite server (checking the connection, turning on hosted links, the links
  themselves, syncing the stream key) were refused with "Backend returned
  HTTP 403" when the server was behind a Cloudflare Tunnel, because
  Cloudflare blocks Python's default request signature. Ignite now
  identifies itself as Ignite.

## 1.6.2

### New

- **API tab.** A new tab with everything for widgets and other streaming
  apps in one place: hosted links and the API on this PC.
- **Hosted links for every overlay.** API tab → Hosted links → Turn On gives
  each overlay a link, like a TikFinity widget link, to paste into OBS,
  Streamlabs Desktop, TikTok LIVE Studio, Meld or vMix on any PC. There's no
  sign-up: Ignite makes your account when you turn it on. If the server stops
  answering, widgets on this PC switch to Ignite here by themselves, and go
  back once it's working again.
- **API for widgets and other streaming apps.** Your own widgets, Stream Deck
  buttons and bots can use Ignite on this PC: every chat event (comments,
  gifts, follows, likes, shares, joins, viewer counts) and Ignite's live state
  over a WebSocket. With the control key they can also run commands: go live,
  switch scenes, save clips, control the music, show alerts, use text to
  speech and Panic. The full reference is in [docs/API.md](docs/API.md).
- **Click-through pop-out chat.** When you lock the pop-out chat, clicks now go
  straight through it to your game. Unlock it from Chat → Lock Pop-out Chat or
  the tray icon's menu.
- **Chat text size.** Make the chat text bigger or smaller with the
  **A− / A+** buttons over the chat, or with Ctrl + mouse wheel. The pop-out
  has its own size in its menu. Both sizes are remembered.

### Changed

- **Less text on screen.** Long explanations now sit behind a **?** at the
  end of their section: hover over it or click it to read them.
- **No emoji.** The seasonal overlay themes no longer put emoji in their
  headings.

### Fixed

- **Autoplay's Start from now changes the artist.** If chat had ever
  requested a song (requests are remembered between streams), autoplay kept
  following those requests and ignored a new **Start from**. Now a new artist
  plays from the next song on, until chat requests something else.
- **Only one Ignite at a time.** Clicking Ignite in the taskbar, the Start
  menu or a shortcut while it's already running now brings the open window
  forward, even from the tray. Before, it started a second copy that fought the
  first one over chat and the overlays.
- **The chat panel shrinks with the window again.** It used to stay at a fixed
  width when the window was made smaller.

## 1.6.1

### New

- **Gift combos flood the chat.** Every tick of a TikTok gift combo now shows
  up in chat (Rose ×1, ×2, ×3…) and fades away after a few seconds. The combo
  still counts once, at its total, for points, goals, leaderboards, action
  rules, text to speech, Discord and gift alerts.
- **"Joined" lines in chat.** When someone joins, "*name* joined" appears in
  chat and fades away. You can turn them off with the new **Joins** filter.
  The viewers Twitch lists when chat first connects only go to the Viewers
  tab, so you don't get hundreds of "joined" lines at once.
- **Clear All Chats.** Find it in the Chat menu, on the Chat panel and in the
  pop-out's ☰ menu. It empties the chat panel, the pop-out chat and the chat
  overlay in OBS. Chat on TikTok, Twitch and YouTube themselves isn't
  touched.
- **Update While Live (experimental).** Change your TikTok title or category
  in the middle of a stream. Turn it on in Preferences → Advanced, then use
  **Update While Live…** on the Stream tab. TikTok can't change a LIVE that's
  already running, so Ignite gets a new key with the new title and category,
  puts it on your OBS output and restarts the LIVE. Viewers see the LIVE end
  and a new one start, and some may not follow. In Ignite it stays one stream:
  goals, points, leaderboards, the stream report and recording carry on. This
  uses Streamlabs' private API, so it may stop working without notice.
- **Music from the web dashboard.** With remote control on and no Now Playing
  overlay open in OBS, a dashboard tab can play the song requests.
- **New chat overlay options:** `&joins=0` hides "joined" lines, and
  `&eventfade=10` sets how many seconds gift and "joined" lines stay (the
  default is 6).
- **Simulator:** a **Gift combo** button, to try the gift flood without going
  live.

### Fixed

- Twitch, YouTube and TikTok chat, the Discord bridge, TikTok voices and
  remote control could fail with `CERTIFICATE_VERIFY_FAILED` on some systems,
  notably the Windows build running under Wine. They now trust the same
  certificates as Ignite's update checker.
