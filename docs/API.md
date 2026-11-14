# Ignite API

Ignite's local API lets anything on your PC use Ignite. That includes your own widgets as Browser
Sources in OBS, Streamlabs Desktop, TikTok LIVE Studio, Meld or vMix, Stream Deck buttons, bots and
scripts. You get every chat event from TikTok, Twitch and YouTube, Ignite's live state, and the same
commands as the buttons in the app.

It runs on Ignite's overlay server, on this PC only:

```
http://localhost:17845/api/v1        HTTP
ws://localhost:17845/api/v1/ws       WebSocket
```

`17845` is the default overlay port (Preferences → Advanced). The **API tab** shows
your exact addresses, with your keys filled in.

## Keys

| Key | Where to find it | Lets you |
|---|---|---|
| **Overlay token** | The `token=` in every overlay URL (API tab → On this PC → *Events*) | Read events and state |
| **Control key** | API tab → On this PC → More → *Control key* | Read, and run commands |

A widget that only shows chat or gifts only needs the overlay token. Only give the control key to
things you trust: with it, they can start and end your LIVE. **New Key…** replaces the control key,
and anything that had the old one can no longer run commands.

Send either key in one of these ways:

- In the URL: `?token=…` (overlay token) or `?key=…` (control key). This is the only way for a
  WebSocket opened from a browser.
- As a header: `Authorization: Bearer …` (either key).

Any web page may connect, whether it's served from another site or opened from a file
(`Origin: null`), as long as it has a key. Requests must be addressed to `localhost`, `127.0.0.1` or
`[::1]` on Ignite's port. Anything else gets `403`, which blocks DNS-rebinding attacks. HTTP responses
carry `Access-Control-Allow-Origin: *`.

The API never sends stream keys, tokens, passwords or file paths.

## WebSocket: `/api/v1/ws`

```js
const ws = new WebSocket("ws://localhost:17845/api/v1/ws?token=YOUR_OVERLAY_TOKEN");
ws.onmessage = (message) => {
  const m = JSON.parse(message.data);
  if (m.type === "event" && m.event === "gift" && !m.data.streaking) {
    console.log(`${m.data.user.name} sent ${m.data.gift} ×${m.data.count}`);
  }
};
```

Every message is a JSON object with a `type`.

### From Ignite

| `type` | When | Fields |
|---|---|---|
| `hello` | First, on connecting | `api` (1), `app`, `version`, `events`, `state` (the kinds), `control` (whether you used the control key), and `commands` (control key only: `{name: {args, help}}`) |
| `state` | Second, on connecting | `state`: every kind below with its latest value |
| `state` | When something changes | `kind`, `data`: that kind's new value |
| `event` | When something happens | `event`, `data`, `simulated` (a test event from the simulator or the `simulate` command), `time` (Unix ms) |
| `result` | Answer to your `command` | `id` (yours), `name`, `ok`, `message` |
| `subscribed` | Answer to your `subscribe` | `events`: what you'll get now |
| `pong` | Answer to your `ping` | `id` (yours) |
| `error` | You sent something it can't read | `message` |

### To Ignite

| `type` | Fields | |
|---|---|---|
| `subscribe` | `events`: a list of event names | Only those events from now on. An empty list or no list means all events again. State messages always come. |
| `command` | `name`, `args` (an object), `id` (anything, echoed back) | Needs the control key. |
| `ping` | `id` | |

If a client falls 2,000 messages behind, Ignite disconnects it instead of holding up the others. When
that happens, reconnect.

## Events

Each `event` message's `data`. Most events include a `user`:

```json
{
  "id": "fan123", "name": "Fan", "avatar": "https://…", "platform": "tiktok", "platform_name": "TikTok",
  "badges": ["moderator", "follower"], "moderator": true, "subscriber": false, "follower": true, "host": false
}
```

`badges` uses `host`, `moderator`, `subscriber` (`member` on YouTube) and `follower`.

| `event` | `data` |
|---|---|
| `comment` | `user`, `text` |
| `gift` | `user`, `gift`, `count`, `diamonds` (one gift's worth), `total_diamonds`, `amount` (money as text, e.g. a Super Chat's `"$5.00"`), `message`, `image`, `streaking` |
| `follow` | `user` |
| `like` | `user`, `count` (this batch, not the stream's total) |
| `share` | `user` |
| `join` | `user`, `listed` (true: already in chat when Ignite connected, from Twitch's member list) |
| `leave` | `user` (Twitch only) |
| `viewers` | `platform`, `count` |
| `stats` | `platform`, `viewers`, `followers`, `likes` (`null`: no news, keep the last value) |
| `live` | `platform`, `live` (Twitch going live or offline) |
| `chat_clear` | `{}`: Clear All Chats was used in Ignite |
| `notice` | `level` (`info` or `error`), `text`: what Ignite shows in its status bar |

**Gift combos:** TikTok sends one `gift` per tick of a combo, with `streaking: true` and `count` so
far. The combo's last gift has `streaking: false` and the total. To count each combo once, count only
gifts with `streaking: false`, as Ignite does. To show the combo building up, use every tick.

Events are moderated the way Ignite's overlays show them. Blocked users' comments are left out, and
blocked words are masked if you set that in Preferences → Moderation.

## State

The latest of each comes in the `state` message on connecting, and again whenever it changes. Over
HTTP, use `GET /api/v1/state` for all of them, or `GET /api/v1/state/<kind>` for one.

| Kind | Value |
|---|---|
| `live` | Whether the TikTok output in OBS is streaming |
| `panic` | Whether panic mode is on |
| `tts_muted` | Whether text to speech is muted |
| `status` | Every connection: `{source: {name, state, detail}}`, with `state` one of `offline`, `connecting`, `connected`, `degraded`, `error` |
| `studio` | OBS: connected, streaming, recording, scenes, the current scene, and the vertical canvas if there is one |
| `queue` | Song requests: `{current, upcoming: [...]}` |
| `playlist` | The playlist that's playing, or `null` |
| `music` | `{needed, volume, paused}` (`needed`: no Now Playing overlay is open to play the music) |
| `goals` | The goal bars, as the Goals overlay shows them |
| `leaderboards` | The leaderboards, as the Leaderboard overlay shows them |
| `game` | The viewer game that's running, or `null` |
| `stats` | Viewers, followers and likes per platform |

## Commands

Commands need the control key. Each one does what the matching button in Ignite does. Commands that
start something, such as going live or saving a clip, answer as soon as it has started. Whether it
then worked shows up as state changes and `notice` events.

Over the WebSocket:

```json
{"type": "command", "id": 1, "name": "switch_scene", "args": {"name": "BRB"}}
```

Over HTTP, use `GET` or `POST /api/v1/commands/<name>`. Arguments can go in the query string, where
`true`/`false` and whole numbers are read as such, and/or in a JSON body:

```sh
curl "http://localhost:17845/api/v1/commands/skip_song?key=YOUR_CONTROL_KEY"
curl -X POST "http://localhost:17845/api/v1/commands/alert?key=YOUR_CONTROL_KEY" \
     -H "Content-Type: application/json" -d '{"title": "Thanks!", "text": "You are awesome"}'
```

The answer is `{"ok": true, "message": "…"}`. On failure it's `{"ok": false, "message": "…"}`, with
status `400` (refused: the message says why), `403` (no control key), `404` (no such command) or `500`.

On a **Stream Deck**, use a *Website* action with the GET address and tick *GET request in background*.

| Command | Arguments | Does |
|---|---|---|
| `toggle_stream` | | Start or stop the TikTok output in OBS (like the Start Streaming button) |
| `start_stream` | | Start the TikTok output in OBS |
| `stop_stream` | | Stop the TikTok output in OBS |
| `send_key` | | Put the current stream key on the chosen OBS output |
| `end_live` | | End the TikTok LIVE and retire its key |
| `relaunch_live` | `title`, `category_id` | Experimental (turn it on in Preferences → Advanced): restart the LIVE with a new title and category |
| `switch_scene` | `name` | Switch to an OBS scene (or a vertical canvas scene) by name |
| `toggle_record` | | Start or stop recording |
| `pause_record` | | Pause or resume the recording |
| `toggle_replay` | | Start or stop the replay buffer |
| `save_replay` | | Save a clip from the replay buffer |
| `toggle_vertical_record` | | Start or stop the vertical canvas recording (Aitum) |
| `toggle_vertical_backtrack` | | Start or stop the vertical canvas backtrack (Aitum) |
| `save_vertical_clip` | | Save a vertical clip from the backtrack (Aitum) |
| `toggle_virtualcam` | | Start or stop the virtual camera |
| `screenshot` | | Save a screenshot of the current scene |
| `refresh_overlays` | | Reload every Ignite overlay |
| `set_overlay_theme` | `theme` | Change the theme of every overlay (`minimal`, `neon`, … or `seasonal`) |
| `connect_chat` | | Connect every chat that has a name set |
| `disconnect_chat` | `platform` | Disconnect one chat (`tiktok`, `twitch`, `youtube`), or all of them |
| `clear_chats` | | Clear the chat panel, the pop-out and the chat overlays |
| `simulate` | `kind`, `user`, `text`, `gift`, `count`, `diamonds`, `platform` | Send a test event (`comment`, `gift`, `follow`, `like`, `share`, `join`) through Ignite. Never counted in points or reports. |
| `alert` | `title`, `text`, `image`, `sound`, `volume` | Show an alert on the Alerts overlay |
| `play_sound` | `name`, `volume` | Play a sound from Ignite's sounds folder through the overlays |
| `say` | `text` | Read text aloud with text to speech |
| `skip_tts` | | Stop the message being read aloud |
| `mute_tts` | `muted` | Mute (`true`) or unmute (`false`) text to speech; with no value, switch |
| `request_song` | `text` | Request a song (a name and artist, or a YouTube link) |
| `skip_song` | | Skip the current song |
| `previous_song` | | Start the song over; again right away, go back one |
| `pause_music` | `paused` | Pause (`true`) or play (`false`) the music; with no value, switch |
| `set_volume` | `volume` | Music volume, 0 to 100 |
| `remove_song` | `item_id` | Remove a song from the queue (`item_id` from `queue`) |
| `clear_queue` | | Empty the song queue |
| `stop_playlist` | | Stop the playlist |
| `run_rule` | `id` | Run an action rule's actions now, like its Test button |
| `panic` | | Panic: mute text to speech, clear alerts, pause music and rules, switch to your BRB scene |
| `resume` | | End panic mode |

OBS commands answer `400` while OBS isn't connected to Ignite. `GET /api/v1` with the control key
lists every command with its arguments, so your code can check what this version of Ignite offers.

## Example: a gift counter widget

Save this as an `.html` file and add it as a Browser Source (local file) in any streaming app:

```html
<!doctype html>
<meta charset="utf-8">
<body style="margin:0; font: 700 48px system-ui; color: white; text-shadow: 0 2px 6px #000">
<div id="total">0 diamonds</div>
<script>
let total = 0;
function connect() {
  const ws = new WebSocket("ws://localhost:17845/api/v1/ws?token=YOUR_OVERLAY_TOKEN");
  ws.onopen = () => ws.send(JSON.stringify({ type: "subscribe", events: ["gift"] }));
  ws.onmessage = (message) => {
    const m = JSON.parse(message.data);
    if (m.type === "event" && !m.data.streaking) {  // Each combo once, at its total.
      total += m.data.total_diamonds;
      document.getElementById("total").textContent = `${total.toLocaleString()} diamonds`;
    }
  };
  ws.onclose = () => setTimeout(connect, 2000);  // Ignite restarted: try again.
}
connect();
</script>
```

## Versions

This is version 1 of the API (`/api/v1`, `"api": 1` in `hello`). New events, state kinds, fields and
commands can be added without a new version, so ignore anything you don't know. Anything removed or
changed in a way that breaks existing code gets a new version, and the old one keeps working for a
while.
