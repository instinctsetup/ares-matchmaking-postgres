/* Ares demo UI. Talks to the FastAPI backend on the same origin:
   REST for queue/stats/matches, one WebSocket per joined party for
   MATCH_FOUND pushes, heartbeats to keep queue entries alive. */

"use strict";

const REGIONS = ["IN", "US-EAST"];
const HEARTBEAT_MS = 60_000; // server purges after 300s without one
const REFRESH_MS = 3_000;

const NAME_POOL = [
  "viper", "ronin", "nova", "jax", "ember", "frost", "sage", "atlas",
  "kira", "drex", "onyx", "pilot", "zeal", "mira", "hades", "echo",
  "blitz", "orca", "lynx", "talon", "rift", "slate", "ivy", "rook",
];

// Parties joined from this tab: party_id -> {players, ws, heartbeat, matched}
const myParties = new Map();

const $ = (id) => document.getElementById(id);

/* ---------------- helpers ---------------- */

function toast(message, ok = false) {
  const el = document.createElement("div");
  el.className = "toast" + (ok ? " ok" : "");
  el.textContent = message;
  $("toast-stack").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

// Server timestamps are ISO strings, with or without a timezone offset.
function serverDate(iso) {
  const hasTz = /([zZ]|[+-]\d{2}:\d{2})$/.test(iso);
  return new Date(hasTz ? iso : iso + "Z");
}

function elapsed(iso) {
  const then = serverDate(iso);
  const secs = Math.max(0, (Date.now() - then.getTime()) / 1000);
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function randomPlayer(region) {
  return {
    username: NAME_POOL[Math.floor(Math.random() * NAME_POOL.length)] +
      Math.floor(Math.random() * 100),
    elo: 900 + Math.floor(Math.random() * 400),
    latency: 10 + Math.floor(Math.random() * 90),
    region: region || REGIONS[Math.floor(Math.random() * REGIONS.length)],
  };
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${options?.method || "GET"} ${path} -> ${res.status}`);
  return res.json();
}

/* ---------------- party builder ---------------- */

function addPlayerRow(player) {
  const row = document.createElement("div");
  row.className = "player-row";
  row.innerHTML = `
    <input class="p-name" placeholder="username" maxlength="24">
    <input class="p-elo" type="number" min="0" max="4000" placeholder="elo">
    <input class="p-latency" type="number" min="1" max="999" placeholder="ms">
    <select class="p-region">${REGIONS.map((r) => `<option>${r}</option>`).join("")}</select>
    <button class="remove-player" type="button" title="Remove">&times;</button>`;
  row.querySelector(".remove-player").onclick = () => row.remove();
  if (player) {
    row.querySelector(".p-name").value = player.username;
    row.querySelector(".p-elo").value = player.elo;
    row.querySelector(".p-latency").value = player.latency;
    row.querySelector(".p-region").value = player.region;
  }
  $("party-rows").appendChild(row);
  return row;
}

function readPartyRows() {
  const players = [];
  for (const row of document.querySelectorAll("#party-rows .player-row")) {
    const username = row.querySelector(".p-name").value.trim();
    if (!username) continue;
    players.push({
      username,
      elo: parseInt(row.querySelector(".p-elo").value || "1000", 10),
      latency: parseInt(row.querySelector(".p-latency").value || "40", 10),
      region: row.querySelector(".p-region").value,
    });
  }
  return players.slice(0, 5);
}

/* ---------------- join + websockets ---------------- */

async function joinParty(players) {
  const { party_id } = await api("/join", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ players }),
  });
  trackParty(party_id, players);
  return party_id;
}

function trackParty(partyId, players) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${partyId}`);
  const entry = { players, ws, matched: false, heartbeat: null, ping: null };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.event === "MATCH_FOUND") onMatchFound(partyId, msg);
  };
  ws.onopen = () => {
    entry.heartbeat = setInterval(
      () => api(`/heartbeat/${partyId}`, { method: "PATCH" }).catch(() => {}),
      HEARTBEAT_MS
    );
    entry.ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 30_000);
  };
  ws.onclose = () => {
    clearInterval(entry.heartbeat);
    clearInterval(entry.ping);
  };

  myParties.set(partyId, entry);
  renderMyParties();
}

function onMatchFound(partyId, msg) {
  const entry = myParties.get(partyId);
  if (entry) {
    entry.matched = true;
    clearInterval(entry.heartbeat);
    renderMyParties();
  }
  $("modal-map").textContent = msg.map;
  $("modal-elo").textContent = Math.round(msg.avg_elo);
  $("modal-team-a").innerHTML = msg.team_a.map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("modal-team-b").innerHTML = msg.team_b.map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("match-modal").classList.remove("hidden");
  refresh();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderMyParties() {
  const box = $("my-parties");
  box.innerHTML = "";
  for (const [pid, entry] of myParties) {
    const chip = document.createElement("div");
    chip.className = "party-chip" + (entry.matched ? " matched" : "");
    chip.innerHTML =
      `<div class="names">${entry.players.map((p) => escapeHtml(p.username)).join(", ")}</div>` +
      `<div class="pid">${pid.slice(0, 8)}&hellip; &middot; ${entry.matched ? "matched" : "queuing"}</div>`;
    box.appendChild(chip);
  }
}

/* ---------------- quick demo ---------------- */

async function quickDemo() {
  // Ten players in one region with close Elos, split into parties whose
  // sizes can pack exactly 5v5 (subset-sum), so the next engine tick matches.
  const region = "IN";
  const sizes = [[5], [3, 2]]; // two parties: 5 and 3+2 -> teams of 5
  const baseElo = 1000 + Math.floor(Math.random() * 200);
  try {
    for (const partySizes of sizes) {
      for (const size of partySizes) {
        const players = Array.from({ length: size }, () => ({
          ...randomPlayer(region),
          elo: baseElo + Math.floor(Math.random() * 60) - 30,
        }));
        await joinParty(players);
      }
    }
    toast("Demo players queued in " + region + " - watch for MATCH_FOUND", true);
  } catch (err) {
    toast("Demo failed: " + err.message);
  }
}

/* ---------------- rendering ---------------- */

function renderQueue(data) {
  $("queue-total").textContent = data.total_count;
  const list = $("queue-list");
  const parties = Object.entries(data.waiting_parties);
  if (!parties.length) {
    list.className = "queue-list empty";
    list.textContent = "Queue is empty";
    return;
  }
  list.className = "queue-list";
  list.innerHTML = "";
  for (const [pid, players] of parties) {
    const el = document.createElement("div");
    el.className = "queue-party";
    el.innerHTML =
      `<div class="party-head"><span>party ${pid.slice(0, 8)}&hellip;</span>` +
      `<span class="wait">waiting ${elapsed(players[0].entered_at)}</span></div>` +
      `<div class="player-chips">` +
      players.map((p) =>
        `<span class="player-chip"><span class="elo">${p.total_elo}</span>` +
        `${escapeHtml(p.username)}<span class="meta">${p.region} &middot; ${p.latency}ms</span></span>`
      ).join("") +
      `</div>`;
    list.appendChild(el);
  }
}

function renderMatches(matches) {
  const list = $("matches-list");
  if (!matches.length) {
    list.className = "matches-list empty";
    list.textContent = "No matches yet";
    return;
  }
  list.className = "matches-list";
  list.innerHTML = "";
  for (const m of matches) {
    const row = document.createElement("div");
    row.className = "match-row";
    const when = serverDate(m.created_at);
    const right = m.winner
      ? `<span class="winner ${m.winner.toLowerCase()}">Team ${m.winner} won</span>`
      : `<span class="pending">resolve:` +
        `<button class="btn small resolve-a" data-id="${m.match_id}" data-w="A">A</button>` +
        `<button class="btn small resolve-b" data-id="${m.match_id}" data-w="B">B</button></span>`;
    row.innerHTML =
      `<div><div class="map">${escapeHtml(m.map)}</div>` +
      `<div class="meta">${m.region} &middot; ${m.match_type} &middot; avg ${Math.round(m.avg_elo)} &middot; ` +
      `${when.toLocaleTimeString()}</div></div>${right}`;
    list.appendChild(row);
  }
  for (const btn of list.querySelectorAll("button[data-id]")) {
    btn.onclick = () => resolveMatch(btn.dataset.id, btn.dataset.w);
  }
}

async function resolveMatch(matchId, winner) {
  try {
    await api(`/match/${matchId}/resolve?winner=${winner}`, { method: "POST" });
    toast(`Match resolved: Team ${winner} takes it (+25 / -20 Elo)`, true);
    refresh();
  } catch (err) {
    toast("Resolve failed: " + err.message);
  }
}

function renderLeaderboard(data) {
  const list = $("leaderboard-list");
  if (!data.top_players.length) {
    list.className = "leaderboard empty";
    list.textContent = "No players yet";
    return;
  }
  list.className = "leaderboard";
  list.innerHTML = data.top_players.map((p, i) =>
    `<li><span><span class="rank">#${i + 1}</span>${escapeHtml(p.username)}</span>` +
    `<span class="lb-elo">${p.total_elo}</span></li>`
  ).join("");
}

function renderStats(s) {
  $("stat-queue").textContent = s.active_queue;
  $("stat-today").textContent = s.matches_today;
  $("stat-success").textContent = s.success_rate;
  $("stat-load").textContent = s.engine_load;
}

function renderThroughput(data) {
  const chart = $("throughput-chart");
  const series = data.throughput;
  if (!series.length) {
    chart.className = "chart empty";
    chart.textContent = "No data yet";
  } else {
    chart.className = "chart";
    const max = Math.max(...series.map((d) => d.matches), 1);
    chart.innerHTML = series.map((d) =>
      `<div class="bar" style="height:${(d.matches / max) * 100}%" ` +
      `data-tip="${d.time} - ${d.matches} match${d.matches === 1 ? "" : "es"}"></div>`
    ).join("");
  }
  $("region-split").innerHTML = data.regions.map((r) =>
    `<span class="region-tag">${r.name} <b>${r.value}</b></span>`
  ).join("");
}

/* ---------------- polling ---------------- */

async function refresh() {
  try {
    const [root, queue, matches, leaderboard, stats, throughput] = await Promise.all([
      api("/"), api("/queue"), api("/matches"),
      api("/leaderboard"), api("/stats"), api("/analytics/throughput"),
    ]);
    $("engine-status").className = "status-pill online";
    $("engine-status-text").textContent = root.status || "online";
    renderQueue(queue);
    renderMatches(matches);
    renderLeaderboard(leaderboard);
    renderStats(stats);
    renderThroughput(throughput);
  } catch (err) {
    $("engine-status").className = "status-pill offline";
    $("engine-status-text").textContent = "engine offline";
  }
}

/* ---------------- wiring ---------------- */

$("add-player").onclick = () => {
  if (document.querySelectorAll("#party-rows .player-row").length < 5) addPlayerRow();
};
$("random-player").onclick = () => {
  const rows = document.querySelectorAll("#party-rows .player-row");
  if (rows.length < 5) addPlayerRow(randomPlayer());
};
$("join-queue").onclick = async () => {
  const players = readPartyRows();
  if (!players.length) return toast("Add at least one player with a username");
  try {
    await joinParty(players);
    toast(`Party of ${players.length} queued`, true);
    $("party-rows").innerHTML = "";
    addPlayerRow();
    refresh();
  } catch (err) {
    toast("Join failed: " + err.message);
  }
};
$("quick-demo").onclick = quickDemo;
$("modal-close").onclick = () => $("match-modal").classList.add("hidden");

addPlayerRow();
refresh();
setInterval(refresh, REFRESH_MS);
