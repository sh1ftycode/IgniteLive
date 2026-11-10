# IgniteLive

One desktop app for running a TikTok LIVE from OBS: stream keys, OBS setup,
TikTok, Twitch and YouTube chat in one feed, chat-requested music, and text to
speech, all in one place.

> Ignite was called ShiftyTool before 1.2. On first start it renames the old
> ShiftyTool settings folder (and the server's data folder) to Ignite, and the
> Windows installer moves the program from `Programs\ShiftyTool` to
> `Programs\Ignite`. Keychain entries, `.shiftyprofile` files and `SHIFTYTOOL_*`
> environment variables keep working.

## Download

Get the latest build from [Releases](https://github.com/sh1ftycode/IgniteLive/releases):

- **Windows:** `Ignite-Setup-<version>.exe` (installer) or the `-windows.zip`.
- **Linux:** `Ignite-<version>-x86_64.AppImage`: `chmod +x` it and run it. Or
  unpack the `-linux.tar.gz` and run `Ignite/Ignite`.

## Why

I was chilling in a VC with colleagues setting up OBS for TikTok and YouTube
multistreaming, and we realized how many buttons and apps it takes to get a
good-looking live setup. Ignite puts all of that into one program.

## What it does

- **Stream:** get a TikTok LIVE server/key through Streamlabs (experimental) or
  paste one manually, send it to OBS, start/stop streaming, end the LIVE.
  Video goes straight from OBS to TikTok. Works with the
  [Aitum Vertical Canvas](https://github.com/Aitum/obs-vertical-canvas) and
  [Aitum Multistream](https://github.com/Aitum/obs-aitum-multistream) plugins:
  pick which output gets the TikTok key, and only that one is started.
- **OBS control:** from the OBS menu and the toolbar: recording (with pause), the
  replay buffer with **Save Clip**, the virtual camera, screenshots, refreshing every
  overlay, and **Add Overlays to OBS…**: tick which overlays (or all of them)
  and which scene; ones already in OBS are skipped. With Aitum Vertical Canvas,
  its recording and backtrack (clips) are there too, and overlays can be
  added straight into vertical scenes (needs obs-websocket 5.7+). Live time, bitrate
  and dropped frames sit in the status bar, and Ignite warns you when the stream
  drops frames, lags, or runs low on disk. Automations (Preferences → OBS): record and run the replay buffer
  while live, and switch to a chosen scene when you go live. Moderators can type
  `!clip` to save a clip, and `!scene name` if you allow it.
- **Chat:** comments, gifts, follows, and viewer count from your TikTok LIVE,
  plus **Twitch** and **YouTube** live chat, merged into one feed with a badge
  for each platform. No Twitch or YouTube login or key: both are read the way
  their own websites show chat. Bits, subs, Super Chats and memberships count as
  gifts. Optionally forward it to a Discord channel through a webhook (File →
  Preferences → Discord).
- **Pop-out chat over your game:** Chat → Pop Out Chat (Ctrl+Shift+C) is a
  small always-on-top window with a see-through background: set how solid it
  is from 0 % (only the chat floats over the game) to 100 % in its ☰ menu.
  Text keeps an edge and numbers sit on small pills, so they read over
  anything. It also has the stats row, a **Viewers** tab, and the music
  buttons, each of which can be turned off there. Lock it in place so it
  can't be moved, scrolled or closed by a stray click (the music buttons
  still work). Games must run windowed or borderless: nothing can draw over
  exclusive fullscreen.
- **Viewers, followers, likes:** the Chat panel and the pop-out show whether
  you're live, the totals, and a chip per platform: watching now, followers
  (YouTube: subscribers), and likes. TikTok's come with its chat; YouTube's
  from the stream's page (subscribers rounded, as YouTube shows them);
  Twitch's from [DecAPI](https://decapi.me), a public service that is sent
  your channel name (Preferences → Chat platforms turns that off).
- **Who's watching:** the **Viewers** tab lists who is here and from which
  platform. No platform publishes its full viewer list, so it's what each one
  reveals: TikTok viewers as they join, plus everyone who chats, likes, gifts,
  follows or shares; Twitch's chat member list (up to about 1,000 names,
  never logged-out viewers); on YouTube only people who chat. Viewers leave
  the list 10 minutes after they were last seen (Twitch: when they leave
  chat), so it's usually shorter than the viewer count.
- **Text to speech:** reads chat aloud, by default as "From {platform}, {user}
  says {message}". Voices: Microsoft Edge's natural voices (default; free, no
  account, about 300 voices), TikTok's own voices (Jessie, C-3PO, Stitch,
  Ghostface…; needs your TikTok `sessionid` cookie), or your computer's offline
  voices. Choose platforms, events, who gets read, a `!tts`-only mode,
  cooldowns, blocked words, speed and pitch. Moderators can type `!skiptts`;
  Chat → Mute pauses it.
- **Make it yours:** every message viewers see or hear (song request replies,
  overlay labels, text to speech, Discord lines) is an editable template with
  `{placeholders}` and a live preview (Preferences → Messages). Commands and
  their prefix can be renamed (Preferences → Commands), and overlays take your
  own accent colours, font and CSS (Preferences → Appearance).
- **Music:** viewers on any platform type `!sr song - artist` or a YouTube (or YouTube
  Music) link. Searches come from YouTube Music's song catalog: official audio
  with album art, no music videos or covers. Songs play in the **Now Playing**
  overlay, in its cover square, with what's next and replies to viewers ("added",
  "not found", "queue full"), plus queue limits, cooldowns, and bans. No API key
  needed. When nobody has requested anything, a public Spotify, YouTube or
  YouTube Music playlist can fill the gaps, or **autoplay** plays songs like
  what chat requested (YouTube Music radio), so it stays in the room's genre.
  **Previous, play/pause and next** are on the Music tab, in the Music menu
  (Ctrl+Left, Ctrl+Space, Ctrl+Right) and on the pop-out chat, with the song's
  time and the volume. Previous starts the song over; pressed again in its
  first seconds, it goes back a song.
- **Your YouTube Music account (experimental):** Music tab → YouTube Music →
  **Connect…** lets your own playlists and Liked Music be the background
  music. YouTube Music has no sign-in for other apps, so you paste the cookie
  of a signed-in music.youtube.com tab (the dialog shows how). Treat it like a
  password: Ignite keeps it in this PC's keychain only, sends it to
  music.youtube.com only, and only reads your playlists; Disconnect removes
  it. Songs still play through the YouTube player, so your own uploads don't
  play. Unofficial: it can break when YouTube changes its site.
- **Actions:** rules that react to your LIVE. Pick a trigger (a gift, optionally
  by name or coin value; a follow; every N likes; a share; a chat command; a
  viewer milestone) and what happens: show an alert, play a sound, read a
  message aloud, switch an OBS scene, show or hide a source for a few seconds,
  or add a song request. Alerts and sounds play in the Alerts overlay, so they
  land in OBS's audio mix. Each rule has a **Test** button.
- **Setup guide:** opens the first time (and from **Help → Setup Guide**, F1). It
  covers your PC, connecting OBS, tuning, TikTok (sign in and get a key without
  leaving the guide), overlays and extras, and ends with what's still open and a
  button to jump back to each. **Tune
  OBS** reads your processor, memory, graphics card and upload speed and
  recommends an output size, frame rate, encoder and bitrate for TikTok. It shows
  *now* vs *recommended* before changing anything, never touches your canvas or
  scenes, and **Undo Tuning** puts OBS back.
- **Test without going live:** View → **Simulate Events** sends fake gifts,
  follows, likes, shares, comments and viewer counts through everything (rules,
  goals, overlays, TTS, song requests). **Send Test** on the Alerts, Chat and
  Goals overlays, **Test** on each goal and rule, and **Play Sound** for sound
  files do the same for one thing at a time. Simulated events never count in
  stream reports, points or leaderboards.
- **Moderation (Preferences → Moderation):** one list of blocked words for text
  to speech, chat and alerts on stream, Discord, song requests and rule alerts.
  It matches whole words and sees through dodges like `b.a.d` or `b4d`. Links
  can be hidden on stream, and blocked viewers are ignored everywhere except your
  own chat panel (mods can `!block @name`). Songs can be refused when they're too
  long, of unknown length, or marked explicit (search then prefers clean
  versions), and refused songs can be banned automatically. Ignite can't
  delete messages on TikTok, Twitch or YouTube themselves.
- **Panic button:** one click or **Ctrl+Shift+P** mutes TTS, clears and holds
  alerts, pauses the music, pauses rules and games, switches to your BRB scene,
  and can mute your mic. **Resume** undoes exactly that.
- **Check before going live:** one button (Stream page or Stream menu) checks
  OBS, the key (present, unexpired, and actually in OBS), the chosen output,
  muted audio, the music player and Alerts overlays, the replay buffer, chat,
  and OBS health, with a **Fix** button where the app can sort it out. It also
  runs when you press **Start Streaming** and only stops you if something would
  break the stream.
- **Auto-clipping:** **Add Auto-Clip Rules** (Actions tab) saves the replay
  buffer 10 s after a big gift or a chat spike (3× the usual chat), so the clip
  includes your reaction. Clips come from OBS's replay buffer, or the Vertical
  Canvas's backtrack when TikTok streams from it. Any rule can save a clip, and
  `!clip` still works.
- **Stream reports:** every stream gets a report on the **Streams** tab: peak
  and average viewers, likes, coins, follows, a viewers-and-chat chart, when
  viewers left and what was playing then, how each song did against the rest of
  the stream, top gifters and chatters, and the clips. Stored locally per minute
  (no chat logs), exportable as CSV.
- **Loyalty points (Viewers tab, off by default):** viewers earn points for
  being around (active in the last 10 minutes), chatting, gifting, following and
  sharing, and spend them on song requests, `!bump` (play my song next), `!say`
  (read my message aloud) and `!sound <name>` (each sound can have its own
  price). `!points` shows a balance; moderators can `!give @name 100`. Failed
  requests are refunded. The Viewers tab lists everyone with all-time totals,
  and rules can give points too. Kept on your computer only.
- **Leaderboards:** a Leaderboard overlay for top gifters, likers or chatters
  (this stream or all time) or the most points. When first place changes it
  shows a "takes the top spot" callout, and "New #1" rules can add a sound or
  TTS.
- **Viewer games (Games tab):** a gift-powered **tug-of-war** (each team has its
  own gifts, or viewers `!join` a team), a **boss fight** where likes and gifts
  deal damage and each defeated boss comes back stronger, and **chat polls**
  (vote with `1`, `!vote 1`, or the option). One runs at a time on the Game
  overlay. Rules can start games and celebrate the winner, each game has a
  **Test** button with pretend players, and stream reports show which games got
  chat going.
- **Goals:** progress bars for likes, coins, follows, shares, or one gift
  (Overlays tab → Goals). When a goal is reached it can stay full, start over,
  or raise its target, and a "Goal reached" rule can celebrate it. Progress
  survives an app restart mid-stream.
- **Profiles:** File → Profiles saves your whole setup (rules, goals, sounds,
  texts, commands, overlay style, TTS and song-request rules) to switch between
  later, or exports it as a `.igniteprofile` file to share. Usernames,
  connections, keys, and passwords are never included. Importing shows what will
  change first.
- **Overlays:** now playing (which plays the music), up next, visualizer, chat,
  gift/follow alerts, and goal bars for OBS, in 21 themes (Minimal, Neon Arcade, Esports HUD,
  Retro 8-bit, Cozy Lo-fi, Synthwave Sunset, Cyberpunk, Frosted Glass, Hacker
  Terminal, Fantasy Quest, Pastel Kawaii, Blood Moon, Deep Ocean,
  Neo-Brutalist, the seasons Spring, Summer, Autumn and Winter, and the
  holidays Halloween, Thanksgiving and Christmas), each with its own
  animations, that switch live from the app. **Seasonal** follows the calendar
  by itself: Halloween from October 15, Thanksgiving through Thanksgiving Day,
  Christmas until December 26, and the season the rest of the year.
  **Browse Themes** (Overlays tab) shows them all side by side with sample songs
  and chat, and **Preview with Sample Data** shows any overlay without going live.
- **Workspace:** a standard desktop window with menus, a toolbar, tabs you can
  close and rearrange (View brings them back), and dockable Chat,
  Connections, and Activity panels. It fits small screens (down to 720×480):
  pages scroll and button rows wrap rather than cutting anything off, and the
  panels keep their share of the window as you resize it. Closing the window keeps Ignite running in
  the system tray (its menu can start the stream, save a clip, or panic); File →
  Quit or the tray's Quit really quits. Uses your system font, in Dark, Light, a
  season (Spring, Summer, Autumn, Winter), a holiday (Halloween, Thanksgiving,
  Christmas), or Seasonal, which follows the calendar like the overlays
  (Preferences → Appearance).
- **Updates:** Ignite checks its GitHub releases every few hours and downloads a
  new version in the background, checked against the release's SHA-256 list. It
  installs when you quit Ignite, or right away from the banner ("Restart and
  Update"), never mid-stream. Works for the Windows installer and the Linux
  AppImage; the zip and tar.gz show a download link instead. Turn it off in
  Preferences → Advanced; Help → Check for Updates checks now.
- **Logs:** everything goes to `logs/ignite.log` in the Ignite folder (File →
  Open Log Folder), rotated at 2 MB, never to a terminal or console window. Set
  `IGNITE_DEBUG=1` to also see it in the terminal.
- **Server (optional):** keeps your stream key for 48 hours even when the app
  is closed, syncs settings between computers, and runs song search. Use
  `https://` for a server on the internet; plain `http://` only works for this
  PC and your local network. If the server doesn't keep stream keys, Ignite
  keeps yours in this PC's keychain instead.
- **Remote control (optional, off by default):** Preferences → Server → *Allow
  remote control from this server's web dashboard*. Ignite connects out to the
  server, so nothing is port-forwarded; the server's dashboard can then start
  and stop the stream, switch scenes, hit Panic and run the music queue. The
  status bar shows **Remote** while it's connected.

> Streamlabs key generation uses undocumented routes and may break without
> notice. Manual keys always work.

See [docs/DESIGN.md](docs/DESIGN.md) for the research, architecture, risks,
and what's been verified.

## Credits

Behavior was researched from these projects. Ignite is an independent
implementation and contains none of their code.

- [Loukious/StreamLabsTikTokStreamKeyGenerator](https://github.com/Loukious/StreamLabsTikTokStreamKeyGenerator)
- [Loukious/TikTokStreamKeyGenerator](https://github.com/Loukious/TikTokStreamKeyGenerator)
- [isaackogan/TikTokLive](https://github.com/isaackogan/TikTokLive)
- [overabstractor/MusicBot](https://github.com/overabstractor/MusicBot)
