# Changelog

What's new in each version of Ignite. The release page for each version
starts with its section from this file (see `.github/workflows/build.yml`).
For versions before 1.6.1, see the
[GitHub releases](https://github.com/sh1ftycode/IgniteLive/releases).

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
