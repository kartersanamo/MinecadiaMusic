(function () {
  const SESSION_ID_KEY = "music_session_id";
  const TOKEN_KEY = "music_token";
  const USER_ID_KEY = "music_user_id";

  function looksLikeSessionId(value) {
    return /^[0-9a-f-]{36}$/i.test(value);
  }

  function isDiscordActivityHost() {
    return /\.discordsays\.com$/i.test(window.location.hostname);
  }

  /** Discord Activity CSP blocks external image hosts — load via same-origin proxy. */
  function artworkUrl(raw) {
    if (!raw) return "";
    if (isDiscordActivityHost()) {
      return `/api/artwork?url=${encodeURIComponent(raw)}`;
    }
    return raw;
  }

  function resolveArtwork(raw, item) {
    if (raw) return raw;
    if (!item) return "";
    const url = externalUrl(item);
    if (!url) return "";
    const match = url.match(YT_VIDEO_ID_RE);
    return match ? `https://i.ytimg.com/vi/${match[1]}/hqdefault.jpg` : "";
  }

  function cssBackgroundUrl(raw) {
    const url = artworkUrl(raw);
    return url ? `url("${url.replace(/"/g, "%22")}")` : "";
  }

  const ART_FALLBACK_SVG =
    '<svg viewBox="0 0 24 24"><path d="M12 3v10.55A4 4 0 1 0 14 14.17V7h6V3h-8z" fill="currentColor"/></svg>';

  function thumbImgHtml(item, className) {
    const src = artworkUrl(resolveArtwork(item?.artwork, item));
    if (!src) {
      return `<div class="${className}">${ART_FALLBACK_SVG}</div>`;
    }
    return (
      `<div class="${className} has-thumb">` +
      `<img src="${escapeHtml(src)}" alt="" loading="lazy" ` +
      `onerror="this.remove();this.parentElement.classList.remove('has-thumb');" />` +
      `${ART_FALLBACK_SVG}</div>`
    );
  }

  const parts = window.location.pathname.split("/").filter(Boolean);
  const params = new URLSearchParams(window.location.search);
  let sessionId = (parts[0] || "").trim();
  const urlToken = (params.get("token") || "").trim();
  let token = urlToken || (sessionStorage.getItem(TOKEN_KEY) || "").trim();
  if (token) sessionStorage.setItem(TOKEN_KEY, token);

  if (!looksLikeSessionId(sessionId)) {
    const storedSessionId = (sessionStorage.getItem(SESSION_ID_KEY) || "").trim();
    if (looksLikeSessionId(storedSessionId)) sessionId = storedSessionId;
  }

  function persistSessionCredentials() {
    if (looksLikeSessionId(sessionId)) {
      sessionStorage.setItem(SESSION_ID_KEY, sessionId);
    }
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    if (userId) sessionStorage.setItem(USER_ID_KEY, userId);
  }

  function clearStoredSessionCredentials() {
    sessionStorage.removeItem(SESSION_ID_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_ID_KEY);
  }

  function getApiBase() {
    return `/api/session/${sessionId}`;
  }
  let ws = null;
  let state = null;
  let userId = sessionStorage.getItem(USER_ID_KEY) || "";
  let positionBase = 0;
  let positionAt = Date.now();
  let progressTimer = null;
  let scrubbing = false;
  let scrubPointerId = null;
  let searchLoading = false;
  let searchPage = 0;
  let searchTotalPages = 0;
  let searchQuery = "";
  let sessionExpired = false;
  let activityBootstrapFailed = false;

  const els = {
    bootFallback: document.getElementById("bootFallback"),
    gate: document.getElementById("gate"),
    gateTitle: document.getElementById("gateTitle"),
    gateMessage: document.getElementById("gateMessage"),
    main: document.getElementById("main"),
    channelLabel: document.getElementById("channelLabel"),
    livePill: document.getElementById("livePill"),
    userLabel: document.getElementById("userLabel"),
    btnLogin: document.getElementById("btnLogin"),
    ambient: document.getElementById("ambient"),
    heroBackdrop: document.getElementById("heroBackdrop"),
    npArt: document.getElementById("npArt"),
    npTitle: document.getElementById("npTitle"),
    npAuthor: document.getElementById("npAuthor"),
    statusChip: document.getElementById("statusChip"),
    loopChip: document.getElementById("loopChip"),
    progressBar: document.getElementById("progressBar"),
    progressTrack: document.getElementById("progressTrack"),
    progressThumb: document.getElementById("progressThumb"),
    npPos: document.getElementById("npPos"),
    npDur: document.getElementById("npDur"),
    btnPlayPause: document.getElementById("btnPlayPause"),
    volume: document.getElementById("volume"),
    volumeLabel: document.getElementById("volumeLabel"),
    loopGroup: document.getElementById("loopGroup"),
    searchInput: document.getElementById("searchInput"),
    btnSearch: document.getElementById("btnSearch"),
    searchEmpty: document.getElementById("searchEmpty"),
    searchResults: document.getElementById("searchResults"),
    searchPagination: document.getElementById("searchPagination"),
    queueList: document.getElementById("queueList"),
    queueEmpty: document.getElementById("queueEmpty"),
    queueCount: document.getElementById("queueCount"),
    queueSubtitle: document.getElementById("queueSubtitle"),
    activityList: document.getElementById("activityList"),
    activityEmpty: document.getElementById("activityEmpty"),
    toast: document.getElementById("toast"),
  };

  const userParam = params.get("user");
  const loggedInParam = params.get("logged_in");
  if (loggedInParam && userParam) {
    userId = userParam;
    sessionStorage.setItem(USER_ID_KEY, userParam);
  } else if (!isDiscordActivityHost()) {
    userId = sessionStorage.getItem(USER_ID_KEY) || "";
  }

  function isSignedIn() {
    return !!userId;
  }

  function hideBootFallback() {
    if (els.bootFallback) {
      els.bootFallback.style.display = "none";
    }
  }

  function paintBootScreen() {
    document.documentElement.style.background = "#0c0d10";
    document.body.style.background = "#0c0d10";
    document.body.style.color = "#f4f5f7";
    if (els.gate) {
      els.gate.hidden = false;
      els.gate.classList.remove("hidden");
    }
    if (els.main) {
      els.main.hidden = true;
      els.main.classList.add("hidden");
    }
  }

  paintBootScreen();

  function showGate(title, message) {
    hideBootFallback();
    if (title) els.gateTitle.textContent = title;
    if (message) els.gateMessage.innerHTML = message;
    els.gate.classList.remove("hidden");
    els.gate.hidden = false;
    els.main.classList.add("hidden");
    els.main.hidden = true;
    setLive(false);
  }

  function showMain() {
    hideBootFallback();
    els.gate.classList.add("hidden");
    els.gate.hidden = true;
    els.main.classList.remove("hidden");
    els.main.hidden = false;
    document.documentElement.style.background = "#0c0d10";
    document.body.style.background = "#0c0d10";
    document.body.style.color = "#f4f5f7";
  }

  function hasSessionCredentials() {
    return looksLikeSessionId(sessionId) && token.length > 0;
  }

  function showSessionExpired() {
    sessionExpired = true;
    clearStoredSessionCredentials();
    if (ws) {
      ws.onclose = null;
      ws.close();
      ws = null;
    }
    showGate(
      "Link expired",
      "This dashboard link is no longer valid. Open the <strong>/music</strong> panel in Discord and use **Launch Dashboard** or the browser link there."
    );
    els.channelLabel.textContent = "Session expired";
  }

  function withTimeout(promise, ms, label) {
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        setTimeout(() => reject(new Error(`${label} timed out`)), ms);
      }),
    ]);
  }

  async function getDiscordSDK() {
    if (window.__DiscordSDK) return window.__DiscordSDK;
    await new Promise((resolve) => {
      if (window.__DiscordSDK) {
        resolve();
        return;
      }
      window.addEventListener("discord-sdk-ready", () => resolve(), { once: true });
      setTimeout(resolve, 3000);
    });
    if (window.__DiscordSDK) return window.__DiscordSDK;
    const mod = await import("/static/discord-embedded-app-sdk.mjs");
    window.__DiscordSDK = mod.DiscordSDK;
    return window.__DiscordSDK;
  }

  async function tryActivityBootstrap() {
    if (!isDiscordActivityHost()) return null;
    const clientId = (
      document.querySelector('meta[name="discord-client-id"]')?.content || ""
    ).trim();
    if (!clientId) return null;

    try {
      const DiscordSDK = await getDiscordSDK();
      const discordSdk = new DiscordSDK(clientId);
      await withTimeout(discordSdk.ready(), 12000, "Discord SDK");

      const { code } = await withTimeout(
        discordSdk.commands.authorize({
          client_id: clientId,
          response_type: "code",
          state: "",
          prompt: "none",
          scope: ["identify"],
        }),
        12000,
        "Discord authorize"
      );

      const tokenRes = await fetch("/api/activity/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const tokenData = await tokenRes.json().catch(() => ({}));
      if (!tokenRes.ok) {
        throw new Error(tokenData.error || tokenRes.statusText);
      }

      const guildId = discordSdk.guildId;
      if (!guildId) {
        throw new Error("Open this Activity from a server voice channel.");
      }

      const bootRes = await fetch(
        `/api/activity/bootstrap?guild_id=${encodeURIComponent(guildId)}`,
        { headers: { Authorization: `Bearer ${tokenData.access_token}` } }
      );
      const boot = await bootRes.json().catch(() => ({}));
      if (!bootRes.ok) {
        throw new Error(boot.error || bootRes.statusText);
      }
      return boot;
    } catch (err) {
      console.error("Activity bootstrap failed", err);
      return null;
    }
  }

  async function refreshActivityCredentials() {
    const activity = await tryActivityBootstrap();
    if (!activity) return false;
    sessionId = activity.sessionId;
    token = activity.token;
    userId = String(activity.userId || userId || "");
    persistSessionCredentials();
    els.userLabel.textContent = "Signed in via Discord Activity";
    els.userLabel.classList.remove("hidden");
    els.btnLogin.classList.add("hidden");
    return true;
  }

  async function validateStoredSession() {
    if (!hasSessionCredentials()) return false;
    try {
      const res = await fetch(`${getApiBase()}/state`, { headers: headers() });
      if (!res.ok) return false;
      const data = await res.json().catch(() => null);
      if (!data) return false;
      return true;
    } catch (_) {
      return false;
    }
  }

  async function bootstrapDashboardSession() {
    if (!isDiscordActivityHost()) return;

    showGate("Connecting…", "Loading your music dashboard.");

    if (await validateStoredSession()) {
      await refreshActivityCredentials().catch(() => {});
      return;
    }

    clearStoredSessionCredentials();
    sessionId = "";
    token = "";

    showGate("Connecting…", "Signing in through Discord…");
    const activity = await tryActivityBootstrap();
    if (!activity) {
      activityBootstrapFailed = true;
      showGate(
        "Activity setup failed",
        "Could not connect to Discord. Run <strong>/music</strong> in this server, then launch the Activity again."
      );
      return;
    }
    sessionId = activity.sessionId;
    token = activity.token;
    userId = String(activity.userId || userId || "");
    persistSessionCredentials();
    els.userLabel.textContent = "Signed in via Discord Activity";
    els.userLabel.classList.remove("hidden");
    els.btnLogin.classList.add("hidden");
  }

  function setSignedInLabel() {
    if (!isSignedIn()) return;
    els.userLabel.textContent = "Signed in with Discord";
    els.userLabel.classList.remove("hidden");
    els.btnLogin.classList.add("hidden");
  }

  function setModifyHint() {
    if (isDiscordActivityHost()) {
      if (!isSignedIn()) {
        els.channelLabel.textContent = "Sign in through Discord Activity to control playback";
      }
      return;
    }
    if (!isSignedIn()) {
      els.channelLabel.textContent = "View-only — sign in with Discord and join the bot's voice channel to control";
      els.btnLogin.classList.remove("hidden");
      return;
    }
    if (state?.voiceChannel) {
      els.channelLabel.textContent = `Join ${state.voiceChannel} in Discord to control playback`;
    }
  }

  function setControlsEnabled(enabled) {
    const disabled = !enabled;
    els.btnPlayPause.disabled = disabled;
    els.volume.disabled = disabled;
    if (els.progressTrack) {
      els.progressTrack.classList.toggle("is-disabled", disabled);
      els.progressTrack.tabIndex = disabled ? -1 : 0;
    }
    document.querySelectorAll(".transport .icon-btn[data-action]").forEach((btn) => {
      btn.disabled = disabled;
    });
    els.loopGroup.querySelectorAll(".segmented-btn").forEach((btn) => {
      btn.disabled = disabled;
    });
    document.querySelectorAll(".queue-item .btn-remove").forEach((btn) => {
      btn.disabled = disabled;
    });
  }

  function setLive(online) {
    els.livePill.classList.toggle("online", online);
    els.livePill.classList.toggle("offline", !online);
    els.livePill.title = online ? "Receiving live updates" : "Reconnecting…";
  }

  function headers(json) {
    const h = { Authorization: `Bearer ${token}` };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  let toastTimer;
  function toast(msg) {
    els.toast.innerHTML = formatDiscordMarkdown(msg);
    els.toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 4200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(getApiBase() + path, {
      ...opts,
      headers: { ...headers(opts.body != null), ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.message || res.statusText);
    return data;
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  const YT_VIDEO_ID_RE =
    /(?:youtube\.com\/(?:watch\?(?:[^&\s]+&)*v=|embed\/|v\/|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/i;

  function externalUrl(item) {
    if (!item) return null;
    const candidates = [item.linkUrl, item.uri, item.identifier];
    for (const candidate of candidates) {
      if (!candidate) continue;
      const value = String(candidate).trim();
      if (value.startsWith("http://") || value.startsWith("https://")) {
        if (value.includes("youtube") || value.includes("youtu.be")) {
          const match = value.match(YT_VIDEO_ID_RE);
          if (match) return `https://www.youtube.com/watch?v=${match[1]}`;
        }
        return value;
      }
      const match = value.match(YT_VIDEO_ID_RE);
      if (match) return `https://www.youtube.com/watch?v=${match[1]}`;
    }
    return null;
  }

  function linkHtml(title, item, { className = "" } = {}) {
    const label = escapeHtml(title || "Unknown");
    const url = externalUrl(item);
    if (!url) return label;
    const cls = className ? ` class="${className}"` : "";
    return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"${cls}>${label}</a>`;
  }

  function formatDiscordMarkdown(text) {
    if (!text) return "";
    let s = escapeHtml(String(text));
    s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    return s;
  }

  function formatMs(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    const sec = s % 60;
    const min = m % 60;
    if (h) return `${h}:${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function formatRelativeTime(unixSeconds) {
    if (!unixSeconds) return "";
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - unixSeconds));
    if (diff < 10) return "just now";
    if (diff < 60) return `${diff}s ago`;
    const min = Math.floor(diff / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  }

  function currentPositionMs() {
    if (scrubbing) return positionBase;
    if (!state?.playing || state?.paused) return state?.positionMs || 0;
    return positionBase + (Date.now() - positionAt);
  }

  function syncPositionFromState() {
    if (scrubbing) return;
    positionBase = state?.positionMs || 0;
    positionAt = Date.now();
  }

  function canScrub() {
    const dur = state?.current?.durationMs || 0;
    return dur > 0 && !!(state?.playing || state?.paused);
  }

  function progressRatioFromMs(pos, dur) {
    if (!dur) return 0;
    return Math.max(0, Math.min(1, pos / dur));
  }

  function applyProgressVisual(ratio) {
    const pct = ratio * 100;
    els.progressBar.style.width = `${pct}%`;
    if (els.progressThumb) els.progressThumb.style.left = `${pct}%`;
    const dur = state?.current?.durationMs || 0;
    els.npPos.textContent = formatMs(Math.round(ratio * dur));
    if (els.progressTrack) {
      els.progressTrack.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
    }
  }

  function updateProgressUi() {
    if (scrubbing) return;
    const cur = state?.current;
    const dur = cur?.durationMs || 0;
    const pos = cur ? currentPositionMs() : 0;
    applyProgressVisual(progressRatioFromMs(pos, dur));
    els.npDur.textContent = cur?.durationText || formatMs(dur);
    if (els.progressTrack) {
      els.progressTrack.classList.toggle("is-disabled", !canScrub());
    }
  }

  function ratioFromPointerEvent(event) {
    const rect = els.progressTrack.getBoundingClientRect();
    if (!rect.width) return 0;
    return Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  }

  async function commitSeek(ratio) {
    const dur = state?.current?.durationMs || 0;
    if (!dur) return;
    const positionMs = Math.round(ratio * dur);
    positionBase = positionMs;
    positionAt = Date.now();
    applyProgressVisual(ratio);
    try {
      await control("seek", { positionMs }, { silent: true });
    } catch (e) {
      toast(e.message);
      syncPositionFromState();
      updateProgressUi();
    }
  }

  function setupProgressScrub() {
    if (!els.progressTrack) return;

    const onPointerDown = (event) => {
      if (!canScrub()) return;
      scrubbing = true;
      scrubPointerId = event.pointerId;
      els.progressTrack.classList.add("is-scrubbing");
      els.progressTrack.setPointerCapture(event.pointerId);
      applyProgressVisual(ratioFromPointerEvent(event));
      event.preventDefault();
    };

    const onPointerMove = (event) => {
      if (!scrubbing || event.pointerId !== scrubPointerId) return;
      applyProgressVisual(ratioFromPointerEvent(event));
    };

    const finishScrub = async (event) => {
      if (!scrubbing || (event && event.pointerId !== scrubPointerId)) return;
      const ratio = ratioFromPointerEvent(event);
      scrubbing = false;
      scrubPointerId = null;
      els.progressTrack.classList.remove("is-scrubbing");
      try {
        els.progressTrack.releasePointerCapture(event.pointerId);
      } catch (_) {
        /* ignore */
      }
      await commitSeek(ratio);
    };

    els.progressTrack.addEventListener("pointerdown", onPointerDown);
    els.progressTrack.addEventListener("pointermove", onPointerMove);
    els.progressTrack.addEventListener("pointerup", finishScrub);
    els.progressTrack.addEventListener("pointercancel", finishScrub);

    els.progressTrack.addEventListener("keydown", (event) => {
      if (!canScrub()) return;
      const dur = state.current.durationMs;
      const step = event.shiftKey ? dur * 0.1 : 5000;
      let pos = currentPositionMs();
      if (event.key === "ArrowRight") pos = Math.min(dur, pos + step);
      else if (event.key === "ArrowLeft") pos = Math.max(0, pos - step);
      else if (event.key === "Home") pos = 0;
      else if (event.key === "End") pos = dur;
      else return;
      event.preventDefault();
      commitSeek(progressRatioFromMs(pos, dur));
    });
  }

  function renderPlaybackControls() {
    const playing = !!state?.playing;
    const paused = !!state?.paused;
    const btn = els.btnPlayPause;
    const iconPause = btn.querySelector(".icon-pause");
    const iconPlay = btn.querySelector(".icon-play");

    if (playing && !paused) {
      btn.dataset.action = "pause";
      btn.title = "Pause";
      iconPause.classList.remove("hidden");
      iconPlay.classList.add("hidden");
      els.statusChip.textContent = "Playing";
      els.statusChip.className = "status-chip playing";
    } else if (paused) {
      btn.dataset.action = "resume";
      btn.title = "Resume";
      iconPause.classList.add("hidden");
      iconPlay.classList.remove("hidden");
      els.statusChip.textContent = "Paused";
      els.statusChip.className = "status-chip paused";
    } else {
      btn.dataset.action = "resume";
      btn.title = "Play";
      iconPause.classList.add("hidden");
      iconPlay.classList.remove("hidden");
      els.statusChip.textContent = "Idle";
      els.statusChip.className = "status-chip";
    }
  }

  function renderLoopControls() {
    const mode = state?.loopMode || "off";
    els.loopGroup.querySelectorAll(".segmented-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.loop === mode);
    });
    if (mode === "off") {
      els.loopChip.classList.add("hidden");
    } else {
      els.loopChip.classList.remove("hidden");
      els.loopChip.textContent = mode === "track" ? "Loop track" : "Loop queue";
    }
  }

  function render() {
    if (!state) return;

    const canModify = isSignedIn();
    setControlsEnabled(canModify);
    setModifyHint();

    if (state.voiceChannel && isSignedIn()) {
      els.channelLabel.textContent = `In ${state.voiceChannel} — you must be in this voice channel to control`;
    } else if (!isSignedIn() && !isDiscordActivityHost()) {
      setModifyHint();
    } else if (state.voiceChannel) {
      els.channelLabel.textContent = `In ${state.voiceChannel}`;
    } else {
      els.channelLabel.textContent = "Not in voice — join a VC in Discord";
    }

    const cur = state.current;
    if (cur) {
      els.npTitle.innerHTML = linkHtml(cur.title, cur, { className: "track-link" });
    } else {
      els.npTitle.textContent = "Nothing playing";
    }

    if (cur) {
      const parts = [];
      if (cur.author) parts.push(escapeHtml(cur.author));
      if (cur.requesterName) parts.push(`queued by ${escapeHtml(cur.requesterName)}`);
      const meta = parts.join(" · ");
      const url = externalUrl(cur);
      els.npAuthor.innerHTML = url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${meta || "Open source"}</a>`
        : meta;
    } else {
      els.npAuthor.textContent = "Search below to start listening";
    }

    const resolvedArt = resolveArtwork(cur?.artwork, cur);
    const artUrl = artworkUrl(resolvedArt);
    const trackUrl = externalUrl(cur);
    els.npArt.classList.toggle("has-art", !!artUrl);
    els.npArt.classList.toggle("is-clickable", !!trackUrl);
    els.npArt.style.backgroundImage = artUrl ? cssBackgroundUrl(resolvedArt) : "";
    els.npArt.onclick = trackUrl
      ? () => window.open(trackUrl, "_blank", "noopener,noreferrer")
      : null;
    els.npArt.title = trackUrl ? "Open source" : "";
    els.heroBackdrop.hidden = !artUrl;
    els.heroBackdrop.style.backgroundImage = artUrl ? cssBackgroundUrl(resolvedArt) : "";
    if (artUrl) {
      els.ambient.style.background = `
        radial-gradient(ellipse 70% 50% at 30% 0%, rgba(241, 196, 15, 0.14), transparent 55%),
        radial-gradient(ellipse 50% 40% at 80% 20%, rgba(88, 101, 242, 0.08), transparent 50%),
        radial-gradient(ellipse 60% 40% at 50% 100%, rgba(241, 196, 15, 0.05), transparent 50%)`;
    }

    if (!scrubbing) {
      syncPositionFromState();
      updateProgressUi();
    }
    renderPlaybackControls();
    renderLoopControls();

    const vol = state.volume ?? 100;
    els.volume.value = vol;
    els.volumeLabel.textContent = `${vol}%`;

    const queue = state.queue || [];
    els.queueCount.textContent = String(queue.length);
    els.queueSubtitle.textContent = queue.length
      ? `${queue.length} track${queue.length === 1 ? "" : "s"} waiting`
      : "Nothing queued";

    els.queueList.innerHTML = "";
    queue.forEach((t, i) => {
      const li = document.createElement("li");
      li.className = "queue-item" + (resolveArtwork(t.artwork, t) ? " has-art" : "");
      const req = t.requesterName ? ` · ${escapeHtml(t.requesterName)}` : "";
      const dur = t.durationText ? ` · ${escapeHtml(t.durationText)}` : "";
      li.innerHTML = `
        <span class="queue-index">${i + 1}</span>
        ${thumbImgHtml(t, "queue-art")}
        <div class="queue-info">
          <p class="queue-title">${linkHtml(t.title, t, { className: "track-link" })}</p>
          <p class="queue-sub">${escapeHtml(t.author || "Unknown")}${dur}${req}</p>
        </div>
        <button type="button" class="btn-remove" title="Remove" aria-label="Remove from queue">
          <svg viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>`;
      li.querySelector(".btn-remove").onclick = () => removeQueue(i);
      els.queueList.appendChild(li);
    });
    els.queueEmpty.style.display = queue.length ? "none" : "";

    const activity = state.activity || [];
    els.activityList.innerHTML = "";
    if (!activity.length) {
      els.activityEmpty.classList.remove("hidden");
    } else {
      els.activityEmpty.classList.add("hidden");
      activity.slice(-6).forEach((entry) => {
        const li = document.createElement("li");
        li.className = "activity-item";
        const who = escapeHtml(entry.actorName || "Someone");
        const text = entry.text || "";
        const rel = formatRelativeTime(entry.at);
        li.innerHTML = `
          <span class="activity-time">${escapeHtml(rel)}</span>
          <div class="activity-body"><strong>${who}</strong> ${formatDiscordMarkdown(text)}</div>`;
        els.activityList.appendChild(li);
      });
    }
  }

  function applyState(next) {
    state = next;
    render();
  }

  async function refresh() {
    try {
      const data = await api("/state");
      applyState(data);
    } catch (e) {
      const msg = String(e.message || "");
      if (/invalid or expired session/i.test(msg)) {
        showSessionExpired();
        return;
      }
      toast(msg);
    }
  }

  async function recoverSessionCredentials() {
    if (isDiscordActivityHost()) {
      return refreshActivityCredentials();
    }
    return validateStoredSession();
  }

  function connectWs() {
    if (sessionExpired) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(
      `${proto}//${location.host}/api/session/${sessionId}/ws?token=${encodeURIComponent(token)}`
    );
    ws.onopen = () => setLive(true);
    ws.onmessage = (ev) => {
      try {
        applyState(JSON.parse(ev.data));
      } catch (_) {}
    };
    ws.onclose = async () => {
      setLive(false);
      if (sessionExpired) return;
      if (await recoverSessionCredentials()) {
        setTimeout(connectWs, 1500);
        return;
      }
      showSessionExpired();
    };
  }

  async function control(action, extra = {}, options = {}) {
    const { silent = false } = options;
    if (!isSignedIn()) {
      toast("Sign in with Discord and join the bot's voice channel to control playback.");
      return;
    }
    try {
      const data = await api("/control", {
        method: "POST",
        body: JSON.stringify({ action, userId, ...extra }),
      });
      if (data.message && !silent) toast(data.message);
      await refresh();
    } catch (e) {
      toast(e.message);
    }
  }

  async function removeQueue(index) {
    if (!isSignedIn()) {
      toast("Sign in with Discord and join the bot's voice channel to modify the queue.");
      return;
    }
    try {
      await api(`/queue/${index}`, {
        method: "DELETE",
        body: JSON.stringify({ userId }),
      });
      toast("Removed from queue");
      await refresh();
    } catch (e) {
      toast(e.message);
    }
  }

  async function addToQueue(item) {
    if (!isSignedIn()) {
      throw new Error("Sign in with Discord and join the bot's voice channel to add music.");
    }
    const isPlaylist = item.kind === "playlist";
    await api("/queue", {
      method: "POST",
      body: JSON.stringify({
        userId,
        kind: isPlaylist ? "playlist" : "track",
        identifier: item.identifier,
        playlistTitle: isPlaylist ? item.title : undefined,
        query: item.uri || item.title,
      }),
    });
    toast(isPlaylist ? `Added playlist (${item.trackCount} tracks)` : "Added to queue");
    await refresh();
  }

  function playlistTracksHtml(tracks) {
    if (!tracks || !tracks.length) return "";
    return (
      '<ol class="playlist-tracks">' +
      tracks
        .map(
          (track, idx) =>
            `<li><span class="playlist-track-index">${idx + 1}.</span> ${linkHtml(track.title, track, { className: "track-link" })} — ${escapeHtml(track.author || "Unknown")}${track.durationText ? ` · ${escapeHtml(track.durationText)}` : ""}</li>`
        )
        .join("") +
      "</ol>"
    );
  }

  function renderSearchPagination(meta) {
    const el = els.searchPagination;
    if (!el) return;
    if (!meta || meta.totalPages <= 1) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    el.classList.remove("hidden");
    const page = meta.page ?? 0;
    const totalPages = meta.totalPages ?? 1;
    const total = meta.total ?? 0;
    el.innerHTML = `
      <button type="button" class="btn btn-ghost btn-sm" id="searchPrev" ${page <= 0 ? "disabled" : ""}>◀ Prev</button>
      <span class="search-page-label">Page ${page + 1} of ${totalPages} · ${total} results</span>
      <button type="button" class="btn btn-ghost btn-sm" id="searchNext" ${page >= totalPages - 1 ? "disabled" : ""}>Next ▶</button>`;
    const prev = el.querySelector("#searchPrev");
    const next = el.querySelector("#searchNext");
    if (prev) prev.onclick = () => runSearch(page - 1);
    if (next) next.onclick = () => runSearch(page + 1);
  }

  function renderSearchResults(results, meta) {
    els.searchResults.innerHTML = "";
    if (!results.length) {
      els.searchResults.classList.add("hidden");
      els.searchEmpty.textContent = "No results — try another search or paste a direct link.";
      els.searchEmpty.classList.remove("hidden");
      renderSearchPagination(null);
      return;
    }
    els.searchEmpty.classList.add("hidden");
    els.searchResults.classList.remove("hidden");

    results.forEach((t) => {
      const isPlaylist = t.kind === "playlist";
      const card = document.createElement("li");
      card.className = "result-card" + (isPlaylist ? " is-playlist" : "");

      const meta = isPlaylist
        ? `${escapeHtml(t.author || "Unknown")} · ${t.trackCount} tracks`
        : `${escapeHtml(t.author || "Unknown")}${t.durationText ? ` · ${escapeHtml(t.durationText)}` : ""}`;

      let tracksHtml = "";
      if (isPlaylist) {
        tracksHtml = playlistTracksHtml(t.tracks || []);
      }

      const openLink = externalUrl(t);
      card.innerHTML = `
        ${thumbImgHtml(t, "result-art")}
        <div class="result-body">
          <p class="result-title">${linkHtml(t.title, t, { className: "track-link" })}</p>
          <p class="result-meta">${meta}${openLink ? ' · <a class="track-link" href="' + escapeHtml(openLink) + '" target="_blank" rel="noopener noreferrer">Open</a>' : ""}</p>
          ${isPlaylist ? '<span class="badge badge-playlist">Playlist</span>' : ""}
          ${tracksHtml}
        </div>
        <div class="result-actions">
          <button type="button" class="btn btn-sm btn-add">${isPlaylist ? `Add all (${t.trackCount})` : "Add"}</button>
        </div>`;

      card.querySelector(".btn-add").onclick = async () => {
        try {
          await addToQueue(t);
        } catch (e) {
          toast(e.message);
        }
      };
      if (openLink) {
        const art = card.querySelector(".result-art");
        if (art) {
          art.classList.add("is-clickable");
          art.title = "Open source";
          art.onclick = () => window.open(openLink, "_blank", "noopener,noreferrer");
        }
      }
      els.searchResults.appendChild(card);
    });
    renderSearchPagination(meta);
  }

  async function runSearch(page = 0) {
    const q = els.searchInput.value.trim();
    if (!q || searchLoading) return;
    if (page === 0 || q !== searchQuery) {
      searchQuery = q;
      searchPage = 0;
      page = 0;
    } else {
      searchPage = page;
    }
    searchLoading = true;
    els.btnSearch.disabled = true;
    els.btnSearch.innerHTML = '<span class="spinner"></span>Searching';
    try {
      const data = await api(`/search?q=${encodeURIComponent(q)}`, {
        method: "POST",
        body: JSON.stringify({ userId, query: q, page, pageSize: 8 }),
      });
      searchPage = data.page ?? page;
      searchTotalPages = data.totalPages ?? 0;
      renderSearchResults(data.results || [], data);
    } catch (e) {
      toast(e.message);
    } finally {
      searchLoading = false;
      els.btnSearch.disabled = false;
      els.btnSearch.textContent = "Search";
    }
  }

  // Event wiring
  document.querySelectorAll(".transport .icon-btn[data-action]").forEach((btn) => {
    if (btn.id === "btnPlayPause") return;
    btn.addEventListener("click", () => control(btn.dataset.action));
  });

  els.btnPlayPause.addEventListener("click", () => control(els.btnPlayPause.dataset.action));

  els.loopGroup.querySelectorAll(".segmented-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return;
      control("loop", { mode: btn.dataset.loop });
    });
  });

  els.volume.addEventListener("input", (e) => {
    els.volumeLabel.textContent = `${e.target.value}%`;
  });

  els.volume.addEventListener("change", (e) => {
    control("volume", { level: parseInt(e.target.value, 10) });
  });

  els.btnSearch.addEventListener("click", () => runSearch(0));
  els.searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch(0);
  });

  els.btnLogin.addEventListener("click", async () => {
    try {
      const data = await api("/oauth");
      window.location.href = data.url;
    } catch (e) {
      toast(e.message || "OAuth not configured — use the link from Discord");
    }
  });

  if (params.get("logged_in")) {
    setSignedInLabel();
    els.btnLogin.classList.add("hidden");
  }

  setSignedInLabel();

  async function startDashboard() {
    await bootstrapDashboardSession();
    if (activityBootstrapFailed) return;

    if (!hasSessionCredentials()) {
      showGate(
        "Session required",
        "Open this page from the <strong>/music</strong> panel in Discord, or use <strong>Launch Dashboard</strong> while in voice."
      );
      return;
    }

    showMain();
    setupProgressScrub();
    refresh();
    connectWs();
    setInterval(refresh, 20000);
    progressTimer = setInterval(() => {
      if (scrubbing) return;
      if (state?.playing && !state?.paused && state?.current) updateProgressUi();
    }, 1000);
  }

  startDashboard().catch((err) => {
    console.error(err);
    hideBootFallback();
    showGate(
      "Unable to load",
      escapeHtml(String(err.message || err)) +
        "<br><br>Leave the Activity and launch it again from the <strong>/music</strong> panel."
    );
  });
})();
