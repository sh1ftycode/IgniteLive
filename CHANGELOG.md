# Changelog

What's new in each version of Ignite. The release page for each version
starts with its section from this file (see `.github/workflows/build.yml`).
For versions before 1.6.1, see the
[GitHub releases](https://github.com/sh1ftycode/IgniteLive/releases).

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
