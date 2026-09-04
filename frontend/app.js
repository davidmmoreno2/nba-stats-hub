// Sitio 100% estático: no hay backend en producción. players.json (índice
// ligero de búsqueda) y players-data.json (stats + comparables ya resueltos)
// los genera build_pages.py con datos precalculados por el motor de similitud.
const OVERLAY_COLORS = ["#22d3ee", "#a78bfa", "#a3e635", "#f472b6"];
const TARGET_COLOR = "#f97316";
const MAX_OVERLAY = 4;

const state = {
  target: null,
  similar: [],
  selectedIds: new Set(),
  chart: null,
};

const el = {
  searchInput: document.getElementById("searchInput"),
  searchResults: document.getElementById("searchResults"),
  emptyState: document.getElementById("emptyState"),
  errorState: document.getElementById("errorState"),
  errorMessage: document.getElementById("errorMessage"),
  result: document.getElementById("result"),
  targetCard: document.getElementById("targetCard"),
  similarList: document.getElementById("similarList"),
  topN: document.getElementById("topN"),
  radarCanvas: document.getElementById("radarChart"),
};

function debounce(fn, delay) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function normalizeText(s) {
  return s
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

let searchIndexPromise = null;
function loadSearchIndex() {
  if (!searchIndexPromise) {
    searchIndexPromise = fetch("players.json").then((r) => r.json());
  }
  return searchIndexPromise;
}

let playersDataPromise = null;
function loadPlayersData() {
  if (!playersDataPromise) {
    playersDataPromise = fetch("players-data.json").then((r) => r.json());
  }
  return playersDataPromise;
}

// ---------- Búsqueda ----------

let activeResultIndex = -1;

const runSearch = debounce(async (query) => {
  if (!query.trim()) {
    el.searchResults.hidden = true;
    return;
  }
  try {
    const index = await loadSearchIndex();
    const qNorm = normalizeText(query);
    const matches = index
      .filter((p) => normalizeText(p.name).includes(qNorm))
      .slice(0, 12)
      .map((p) => ({ player_id: p.id, player_name: p.name, team_abbreviation: p.team, pts: p.pts }));
    renderSearchResults(matches);
  } catch (err) {
    el.searchResults.hidden = true;
  }
}, 250);

function renderSearchResults(players) {
  el.searchResults.innerHTML = "";
  activeResultIndex = -1;
  if (players.length === 0) {
    el.searchResults.hidden = true;
    return;
  }
  for (const p of players) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${p.player_name}</span><span class="team">${p.team_abbreviation} · ${p.pts} pts</span>`;
    li.addEventListener("click", () => selectResult(p));
    li.addEventListener("mouseenter", () => setActiveResult(Array.from(el.searchResults.children).indexOf(li)));
    el.searchResults.appendChild(li);
  }
  el.searchResults.hidden = false;
}

function selectResult(p) {
  el.searchInput.value = p.player_name;
  el.searchResults.hidden = true;
  loadPlayer(p.player_id);
}

function setActiveResult(index) {
  const items = el.searchResults.children;
  if (items.length === 0) return;
  activeResultIndex = ((index % items.length) + items.length) % items.length;
  for (let i = 0; i < items.length; i++) {
    items[i].classList.toggle("active", i === activeResultIndex);
  }
  items[activeResultIndex].scrollIntoView({ block: "nearest" });
}

el.searchInput.addEventListener("input", (e) => runSearch(e.target.value));

el.searchInput.addEventListener("keydown", (e) => {
  if (el.searchResults.hidden || el.searchResults.children.length === 0) return;

  if (e.key === "ArrowDown") {
    e.preventDefault();
    setActiveResult(activeResultIndex + 1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    setActiveResult(activeResultIndex - 1);
  } else if (e.key === "Enter") {
    e.preventDefault();
    const index = activeResultIndex >= 0 ? activeResultIndex : 0;
    el.searchResults.children[index]?.dispatchEvent(new Event("click"));
  } else if (e.key === "Escape") {
    el.searchResults.hidden = true;
  }
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) {
    el.searchResults.hidden = true;
  }
});

el.topN.addEventListener("change", () => {
  if (state.target) loadPlayer(state.target.player_id);
});

// ---------- Carga de jugador + comparables ----------

async function loadPlayer(playerId) {
  showLoadingState();
  try {
    const n = Number(el.topN.value);
    const table = await loadPlayersData();
    const target = table[playerId];
    if (!target) throw new Error(`jugador ${playerId} no encontrado`);

    state.target = target;
    state.similar = target.similar.slice(0, n).map((s) => ({ ...table[s.player_id], similarity: s.similarity }));
    state.selectedIds = new Set(state.similar.slice(0, 3).map((p) => p.player_id));

    renderTargetCard();
    renderSimilarList();
    showResultState();
    renderRadar();
  } catch (err) {
    showErrorState(err.message);
  }
}

function showLoadingState() {
  el.emptyState.hidden = true;
  el.errorState.hidden = true;
  el.result.hidden = true;
}

function showResultState() {
  el.emptyState.hidden = true;
  el.errorState.hidden = true;
  el.result.hidden = false;
}

function showErrorState(message) {
  el.emptyState.hidden = true;
  el.result.hidden = true;
  el.errorState.hidden = false;
  el.errorMessage.textContent = `No se pudo cargar el jugador: ${message}`;
}

// ---------- Render: tarjeta del jugador objetivo ----------

function renderTargetCard() {
  const t = state.target;
  el.targetCard.innerHTML = `
    <div class="name-block">
      <h1>${t.player_name}</h1>
      <div class="sub">${t.team_abbreviation} · ${t.age ?? "?"} años · Temporada ${t.season}</div>
      ${smallSampleBadge(t)}
    </div>
    <div class="stat-pill-row">
      ${statPill("PTS", t.traditional.pts)}
      ${statPill("REB", t.traditional.reb)}
      ${statPill("AST", t.traditional.ast)}
      ${statPill("TS%", pct(t.advanced.ts_pct))}
      ${statPill("USG%", pct(t.advanced.usg_pct))}
    </div>
  `;
}

function statPill(label, value) {
  return `<div class="stat-pill"><div class="value">${value ?? "-"}</div><div class="label">${label}</div></div>`;
}

function pct(v) {
  if (v === null || v === undefined) return null;
  return `${Math.round(v * 100)}`;
}

// Aviso de muestra insuficiente (GP/MIN bajos): el backend calcula el umbral
// (mismo que el pool de comparables fiables), aquí solo se renderiza.
function smallSampleBadge(player) {
  if (!player.small_sample) return "";
  return `<div class="small-sample-badge" title="Menos de 20 partidos o 15 minutos por partido: estas stats pueden no reflejar su nivel real">
    ⚠️ Muestra pequeña (${player.gp} partido${player.gp === 1 ? "" : "s"})
  </div>`;
}

// ---------- Render: lista de comparables ----------

function renderSimilarList() {
  el.similarList.innerHTML = "";
  state.similar.forEach((p, idx) => {
    const li = document.createElement("li");
    li.className = "similar-item";

    const colorIdx = [...state.selectedIds].indexOf(p.player_id);
    const swatchColor = colorIdx >= 0 ? OVERLAY_COLORS[colorIdx % OVERLAY_COLORS.length] : "transparent";

    li.innerHTML = `
      <input type="checkbox" data-id="${p.player_id}" ${state.selectedIds.has(p.player_id) ? "checked" : ""} />
      <span class="swatch" style="background:${swatchColor}"></span>
      <div class="info">
        <div class="name">${p.player_name}</div>
        <div class="meta">${p.team_abbreviation} · ${p.traditional.pts} pts / ${p.traditional.reb} reb / ${p.traditional.ast} ast</div>
        ${smallSampleBadge(p)}
      </div>
      <div class="similarity">${p.similarity}%</div>
      <button data-compare-id="${p.player_id}">Comparar</button>
    `;

    li.querySelector("input[type=checkbox]").addEventListener("change", (e) => {
      toggleOverlay(p.player_id, e.target.checked, e.target);
    });
    li.querySelector("button").addEventListener("click", () => loadPlayer(p.player_id));

    el.similarList.appendChild(li);
  });
}

function toggleOverlay(playerId, checked, checkboxEl) {
  if (checked) {
    if (state.selectedIds.size >= MAX_OVERLAY) {
      checkboxEl.checked = false;
      return;
    }
    state.selectedIds.add(playerId);
  } else {
    state.selectedIds.delete(playerId);
  }
  renderSimilarList();
  renderRadar();
}

// ---------- Render: radar chart ----------

function renderRadar() {
  const labels = Object.keys(state.target.radar);

  const datasets = [
    {
      label: state.target.player_name,
      data: labels.map((axis) => state.target.radar[axis]),
      borderColor: TARGET_COLOR,
      backgroundColor: hexToRgba(TARGET_COLOR, 0.2),
      borderWidth: 2,
      pointRadius: 2,
    },
  ];

  [...state.selectedIds].forEach((id, idx) => {
    const player = state.similar.find((p) => p.player_id === id);
    if (!player) return;
    const color = OVERLAY_COLORS[idx % OVERLAY_COLORS.length];
    datasets.push({
      label: player.player_name,
      data: labels.map((axis) => player.radar[axis]),
      borderColor: color,
      backgroundColor: hexToRgba(color, 0.08),
      borderWidth: 2,
      pointRadius: 2,
    });
  });

  if (state.chart) {
    state.chart.destroy();
  }

  state.chart = new Chart(el.radarCanvas, {
    type: "radar",
    data: { labels, datasets },
    options: {
      responsive: true,
      scales: {
        r: {
          min: 0,
          max: 100,
          ticks: { display: false, stepSize: 25 },
          grid: { color: "#223047" },
          angleLines: { color: "#223047" },
          pointLabels: { color: "#e6edf5", font: { size: 12 } },
        },
      },
      plugins: {
        legend: { position: "bottom", labels: { color: "#e6edf5", boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: percentil ${Math.round(ctx.parsed.r)}`,
          },
        },
      },
    },
  });
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
