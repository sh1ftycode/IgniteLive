# Changelog

What's new in each version of Ignite. The release page for each version
starts with its section from this file (see `.github/workflows/build.yml`).
For versions before 1.6.1, see the
[GitHub releases](https://github.com/sh1ftycode/IgniteLive/releases).

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
