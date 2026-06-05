(function () {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const sessionId = parts[0] || "";
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") || sessionStorage.getItem("music_token") || "";
  if (token) sessionStorage.setItem("music_token", token);

  const apiBase = `/api/session/${sessionId}`;
  let ws = null;
  let state = null;
  let userId = sessionStorage.getItem("music_user_id") || "";

  function headers(json) {
    const h = { Authorization: `Bearer ${token}` };
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 3000);
  }

  async function api(path, opts = {}) {
    const res = await fetch(apiBase + path, {
      ...opts,
      headers: { ...headers(opts.body != null), ...(opts.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.message || res.statusText);
    return data;
  }

  function render() {
    if (!state) return;
    document.getElementById("channelLabel").textContent = state.voiceChannel
      ? `Voice: ${state.voiceChannel}`
      : "Not in a voice channel";
    const cur = state.current;
    document.getElementById("npTitle").textContent = cur ? cur.title : "Nothing playing";
    document.getElementById("npAuthor").textContent = cur ? cur.author : "";
    const art = document.getElementById("npArt");
    art.style.backgroundImage = cur && cur.artwork ? `url(${cur.artwork})` : "none";
    const dur = cur ? cur.durationMs : 0;
    const pos = state.positionMs || 0;
    const pct = dur > 0 ? Math.min(100, (pos / dur) * 100) : 0;
    document.getElementById("progressBar").style.width = `${pct}%`;
    document.getElementById("npTime").textContent = cur
      ? `${formatMs(pos)} / ${cur.durationText}`
      : "";
    document.getElementById("loopSelect").value = state.loopMode || "off";
    document.getElementById("volume").value = state.volume ?? 100;

    const list = document.getElementById("queueList");
    list.innerHTML = "";
    (state.queue || []).forEach((t, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${i + 1}. ${escapeHtml(t.title)} <small class="muted">${escapeHtml(t.author)}</small></span>`;
      const rm = document.createElement("button");
      rm.textContent = "Remove";
      rm.className = "secondary";
      rm.onclick = () => removeQueue(i);
      li.appendChild(rm);
      list.appendChild(li);
    });
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function formatMs(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  async function refresh() {
    try {
      state = await api("/state");
      render();
    } catch (e) {
      toast(e.message);
    }
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/api/session/${sessionId}/ws?token=${encodeURIComponent(token)}`);
    ws.onmessage = (ev) => {
      try {
        state = JSON.parse(ev.data);
        render();
      } catch (_) {}
    };
    ws.onclose = () => setTimeout(connectWs, 3000);
  }

  async function control(action, extra = {}) {
    try {
      const data = await api("/control", {
        method: "POST",
        body: JSON.stringify({ action, userId, ...extra }),
      });
      toast(data.message || (data.ok === false ? data.error : null) || "OK");
      await refresh();
    } catch (e) {
      toast(e.message);
    }
  }

  async function removeQueue(index) {
    try {
      await api(`/queue/${index}`, {
        method: "DELETE",
        body: JSON.stringify({ userId }),
      });
      await refresh();
    } catch (e) {
      toast(e.message);
    }
  }

  document.querySelectorAll(".transport button").forEach((btn) => {
    btn.addEventListener("click", () => control(btn.dataset.action));
  });

  document.getElementById("loopSelect").addEventListener("change", (e) => {
    control("loop", { mode: e.target.value });
  });

  document.getElementById("volume").addEventListener("change", (e) => {
    control("volume", { level: parseInt(e.target.value, 10) });
  });

  document.getElementById("btnSearch").addEventListener("click", async () => {
    const q = document.getElementById("searchInput").value.trim();
    if (!q) return;
    try {
      const data = await api(`/search?q=${encodeURIComponent(q)}`, {
        method: "POST",
        body: JSON.stringify({ userId, query: q }),
      });
      const ul = document.getElementById("searchResults");
      ul.innerHTML = "";
      (data.results || []).forEach((t) => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${escapeHtml(t.title)} — ${escapeHtml(t.author)}</span>`;
        const add = document.createElement("button");
        add.textContent = "Add";
        add.onclick = async () => {
          try {
            await api("/queue", {
              method: "POST",
              body: JSON.stringify({ userId, query: t.uri || t.title }),
            });
            toast("Added to queue");
            await refresh();
          } catch (e) {
            toast(e.message);
          }
        };
        li.appendChild(add);
        ul.appendChild(li);
      });
    } catch (e) {
      toast(e.message);
    }
  });

  document.getElementById("btnLogin").addEventListener("click", async () => {
    try {
      const data = await api("/oauth");
      window.location.href = data.url;
    } catch (e) {
      toast(e.message || "OAuth not configured — use token link from Discord");
    }
  });

  if (params.get("logged_in")) {
    document.getElementById("userLabel").textContent = "Signed in with Discord";
  }

  if (!sessionId || !token) {
    toast("Missing session or token in URL");
  } else {
    refresh();
    connectWs();
    setInterval(refresh, 15000);
  }
})();
