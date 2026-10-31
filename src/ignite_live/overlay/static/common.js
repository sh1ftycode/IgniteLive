// Shared by every overlay page: connection to Ignite, theme switching, the streamer's own
// texts and style, small DOM helpers. All text is set with textContent, never innerHTML.
"use strict";
const ST = (() => {
  const params = new URLSearchParams(location.search);
  const token = params.get("token") || "";
  const pinnedTheme = params.get("theme"); // &theme=… pins this overlay to one theme
  const demo = params.get("demo") === "1"; // &demo=1: sample songs and chat, for previews without going live
  const handlers = {};
  let socket = null;
  let texts = {};
  let customCss = null;

  function applyTheme(theme) {
    document.documentElement.dataset.theme = pinnedTheme || theme || "minimal";
  }

  // Preferences → Appearance: accent colours, font, and custom CSS on top of the theme.
  // Inline custom properties on <html> win over every theme's own values.
  function applyStyle(style) {
    style = style || {};
    const root = document.documentElement.style;
    const set = (name, value) => (value ? root.setProperty(name, value) : root.removeProperty(name));
    set("--accent", style.accent);
    set("--accent-2", style.accent2);
    const font = style.font ? `"${style.font}", system-ui, sans-serif` : "";
    set("--font", font);
    set("--title-font", font);
    if (!customCss) {
      customCss = document.createElement("style");
      document.head.append(customCss);
    }
    customCss.textContent = style.css || "";
  }

  // Message templates from Preferences → Messages: "{name}" placeholders, filled as plain text.
  function fill(template, values) {
    return String(template).replace(/\{(\w+)\}/g, (all, name) => (name in values ? String(values[name]) : all));
  }

  function t(key, fallback, values) {
    return fill(key in texts ? texts[key] : fallback, { command: texts.command || "!sr song - artist", ...(values || {}) });
  }

  // Like t(), but returns nodes with each filled placeholder in <b>.
  function tBold(key, fallback, values) {
    values = { command: texts.command || "!sr song - artist", ...(values || {}) };
    const template = String(key in texts ? texts[key] : fallback);
    const nodes = [];
    let last = 0;
    template.replace(/\{(\w+)\}/g, (all, name, at) => {
      nodes.push(template.slice(last, at));
      nodes.push(name in values ? el("b", null, String(values[name])) : all);
      last = at + all.length;
      return all;
    });
    nodes.push(template.slice(last));
    return nodes.filter((node) => node !== "");
  }

  function on(type, fn) {
    (handlers[type] = handlers[type] || []).push(fn);
  }

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }

  function connect() {
    const page = location.pathname.split("/").pop(); // Lets the app's checklist see which overlays are open.
    socket = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}&page=${encodeURIComponent(page)}`
                           + (demo ? "&preview=1" : ""));
    socket.onmessage = (event) => {
      let message = JSON.parse(event.data);
      if (demo && message.type === "state") message = { ...message, queue: DEMO_QUEUE };
      if (demo && ["chat", "progress", "goals", "leaderboards", "leader", "game", "game_over"].includes(message.type)) return; // Only sample data in a preview.
      if (message.type === "state") {
        applyTheme(message.theme);
        applyStyle(message.style);
        texts = message.texts || {};
      }
      (handlers[message.type] || []).forEach((fn) => fn(message));
    };
    socket.onclose = () => setTimeout(connect, 2000);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function clock(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function avatar(name, url) {
    const box = el("div", "avatar", url ? null : (name || "?").slice(0, 1).toUpperCase());
    if (url) {
      const img = el("img");
      img.referrerPolicy = "no-referrer";
      img.src = url; // The app only forwards the platforms' own https image URLs.
      img.onerror = () => { box.textContent = (name || "?").slice(0, 1).toUpperCase(); };
      box.append(img);
    }
    return box;
  }

  // Preview mode: a fake queue, progress, and a loop of chat, gifts and follows.
  const DEMO_QUEUE = {
    current: { id: 1, video_id: "demo", title: "Blinding Lights", channel: "The Weeknd", requested_by: "Ember",
               duration_s: 200, image: "" },
    upcoming: [
      { id: 2, video_id: "demo2", title: "Levitating", channel: "Dua Lipa", requested_by: "NightOwl", duration_s: 203 },
      { id: 3, video_id: "demo3", title: "As It Was", channel: "Harry Styles", requested_by: "Bo", duration_s: 167 },
      { id: 4, video_id: "demo4", title: "Heat Waves", channel: "Glass Animals", requested_by: "Rin", duration_s: 238 },
    ],
  };
  const DEMO_CHAT = [
    { kind: "comment", name: "NightOwl", text: "this song is fire", platform: "tiktok", platform_name: "TikTok", badges: [] },
    { kind: "comment", name: "Bo", text: "!sr Levitating - Dua Lipa", platform: "twitch", platform_name: "Twitch", badges: ["SUB"] },
    { kind: "gift", name: "Rin", gift: "Rose", count: 5, diamonds: 5, platform: "tiktok", platform_name: "TikTok", badges: [] },
    { kind: "comment", name: "ModMan", text: "welcome in everyone!", platform: "youtube", platform_name: "YouTube", badges: ["MOD"] },
    { kind: "gift", name: "Kai", gift: "Super Chat", count: 1, amount: "$5.00", message: "play my song next", platform: "youtube",
      platform_name: "YouTube", badges: [] },
    { kind: "follow", name: "NewFriend", platform: "tiktok", platform_name: "TikTok", badges: [] },
  ];
  const DEMO_GOALS = [
    { id: "demo-likes", name: "Like goal", label: "6,420 / 10,000 likes", current: 6420, target: 10000, percent: 64.2, done: false },
    { id: "demo-gift", name: "Rose goal", label: "37 / 50 Rose", current: 37, target: 50, percent: 74, done: false },
  ];
  function dispatch(message) {
    (handlers[message.type] || []).forEach((fn) => fn(message));
  }
  function startDemo() {
    let position = 64, line = 0;
    setInterval(() => {
      position = (position + 0.5) % 200;
      dispatch({ type: "progress", item_id: 1, position, duration: 200, playing: true });
    }, 500);
    const next = () => { dispatch({ type: "chat", ...DEMO_CHAT[line % DEMO_CHAT.length] }); line += 1; };
    let likes = 6420;
    setInterval(() => {
      likes = likes >= 10000 ? 6420 : likes + 180;
      const goals = DEMO_GOALS.map((g) => ({ ...g }));
      Object.assign(goals[0], { current: likes, label: `${likes.toLocaleString()} / 10,000 likes`,
                                percent: Math.min(100, likes / 100), done: likes >= 10000 });
      dispatch({ type: "goals", goals });
    }, 1200);
    dispatch({ type: "goals", goals: DEMO_GOALS });
    const names = ["NightOwl", "Rin", "Bo", "Kai", "Luna"];
    const boards = {};
    for (const [kind, unit] of [["gifters", "coins"], ["likers", "likes"], ["chatters", "messages"], ["points", "points"]]) {
      for (const period of ["stream", "alltime"]) {
        boards[`${kind}:${period}`] = { title: `Top ${kind}`, unit,
          rows: names.map((name, i) => ({ name, platform: "tiktok", value: (5 - i) * 1200 })) };
      }
    }
    dispatch({ type: "leaderboards", boards });
    const top = names.slice(0, 3).map((name, i) => ({ name, value: (3 - i) * 400 }));
    const games = [
      { kind: "tug", left: { name: "Red", color: "#e5484d", score: 1450, how: "Rose" },
        right: { name: "Blue", color: "#3fa7f5", score: 980, how: "TikTok" }, position: -0.47, remaining: 94, top },
      { kind: "boss", name: "The Boss", image: "", level: 2, hp: 820, max_hp: 1500, defeated: 1,
        last_hit: "Rin −120", remaining: null, top },
      { kind: "poll", question: "What should I play next?", how: "Type 1–3 or !vote 1", total: 37, remaining: 41,
        options: [{ label: "Minecraft", votes: 19, percent: 51.4 }, { label: "Fortnite", votes: 12, percent: 32.4 },
                  { label: "Just chatting", votes: 6, percent: 16.2 }] },
    ];
    let shown = 0;
    dispatch({ type: "game", game: games[0] });
    setInterval(() => { shown = (shown + 1) % games.length; dispatch({ type: "game", game: games[shown] }); }, 6000);
    setInterval(() => Object.keys(boards).forEach((board) => dispatch({ type: "leader", board, name: "NightOwl", value: 6000 })), 9000);
    for (let i = 0; i < 3; i += 1) next();
    setInterval(next, 2500);
    // Without the app running, still show something.
    setTimeout(() => { if (!socket || socket.readyState !== WebSocket.OPEN) dispatch({ type: "state", queue: DEMO_QUEUE, volume: 0 }); }, 1500);
  }

  applyTheme(pinnedTheme);
  window.addEventListener("load", connect);
  if (demo) window.addEventListener("load", startDemo);
  // A small platform badge (Twitch, YouTube…). &platforms=all also tags TikTok, &platforms=0 hides them.
  function platformBadge(m) {
    const mode = params.get("platforms");
    if (!m.platform || mode === "0" || (m.platform === "tiktok" && mode !== "all")) return null;
    return el("span", `badge platform platform-${m.platform}`, m.platform_name || m.platform);
  }

  return { on, send, el, clock, avatar, params, t, tBold, platformBadge, demo };
})();
