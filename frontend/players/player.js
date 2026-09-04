// Hidrata la página individual de un jugador: dibuja el radar chart (los
// datos ya vienen embebidos en la página por build_pages.py) y hace que el
// buscador del header navegue a la página estática de cada jugador.

(function () {
  // Sitio 100% estático: sin backend en producción. players.json (generado
  // por build_pages.py) trae id/nombre/slug/equipo/pts de los 506 jugadores.

  function renderRadar() {
    const canvas = document.getElementById("radarChart");
    if (!canvas || !window.PLAYER_RADAR) return;

    const labels = Object.keys(window.PLAYER_RADAR);
    new Chart(canvas, {
      type: "radar",
      data: {
        labels,
        datasets: [
          {
            label: window.PLAYER_NAME,
            data: labels.map((axis) => window.PLAYER_RADAR[axis]),
            borderColor: "#f97316",
            backgroundColor: "rgba(249, 115, 22, 0.2)",
            borderWidth: 2,
            pointRadius: 2,
          },
        ],
      },
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
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: percentil ${Math.round(ctx.parsed.r)}`,
            },
          },
        },
      },
    });
  }

  // --- Buscador del header: mismo look&feel que el comparador (index.html),
  // pero al elegir un resultado navega a la página estática de ese jugador
  // en vez de cargarlo inline. ---

  let playersPromise = null;
  function loadPlayers() {
    if (!playersPromise) {
      playersPromise = fetch("../players.json").then((r) => r.json());
    }
    return playersPromise;
  }

  function debounce(fn, delay) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delay);
    };
  }

  function setupSearch() {
    const input = document.getElementById("searchInput");
    const results = document.getElementById("searchResults");
    if (!input || !results) return;

    let activeIndex = -1;

    const runSearch = debounce(async (query) => {
      if (!query.trim()) {
        results.hidden = true;
        return;
      }
      try {
        const all = await loadPlayers();
        const qNorm = normalizeForSearch(query);
        const matches = all.filter((p) => normalizeForSearch(p.name).includes(qNorm)).slice(0, 12);
        renderResults(matches);
      } catch (err) {
        results.hidden = true;
      }
    }, 250);

    function goToResult(slug) {
      if (slug) window.location.href = `${slug}.html`;
    }

    function setActive(index) {
      const items = results.children;
      if (items.length === 0) return;
      activeIndex = ((index % items.length) + items.length) % items.length;
      for (let i = 0; i < items.length; i++) {
        items[i].classList.toggle("active", i === activeIndex);
      }
      items[activeIndex].scrollIntoView({ block: "nearest" });
    }

    function renderResults(players) {
      results.innerHTML = "";
      activeIndex = -1;
      if (players.length === 0) {
        results.hidden = true;
        return;
      }
      players.forEach((p, i) => {
        const li = document.createElement("li");
        li.innerHTML = `<span>${p.name}</span><span class="team">${p.team} · ${p.pts} pts</span>`;
        li.addEventListener("click", () => goToResult(p.slug));
        li.addEventListener("mouseenter", () => setActive(i));
        results.appendChild(li);
      });
      results.hidden = false;
    }

    input.addEventListener("input", (e) => runSearch(e.target.value));

    input.addEventListener("keydown", (e) => {
      if (results.hidden || results.children.length === 0) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(activeIndex + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(activeIndex - 1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const index = activeIndex >= 0 ? activeIndex : 0;
        results.children[index]?.dispatchEvent(new Event("click"));
      } else if (e.key === "Escape") {
        results.hidden = true;
      }
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrap")) results.hidden = true;
    });
  }

  // --- Filtro en vivo del índice de jugadores (/players/index.html). No-op
  // en el resto de páginas, que no tienen #indexFilter. ---
  function normalizeForSearch(s) {
    return s
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function setupIndexFilter() {
    const input = document.getElementById("indexFilter");
    if (!input) return;

    const items = Array.from(document.querySelectorAll(".player-index-list li"));
    const groups = Array.from(document.querySelectorAll(".index-letter-group"));
    const noMatches = document.getElementById("indexNoMatches");

    input.addEventListener("input", () => {
      const query = normalizeForSearch(input.value.trim());
      let anyVisible = false;

      for (const li of items) {
        const match = !query || li.dataset.name.includes(query);
        li.hidden = !match;
        if (match) anyVisible = true;
      }
      for (const group of groups) {
        const hasVisible = group.querySelector("li:not([hidden])");
        group.hidden = !hasVisible;
      }
      if (noMatches) noMatches.hidden = anyVisible;
    });
  }

  renderRadar();
  setupSearch();
  setupIndexFilter();
})();
