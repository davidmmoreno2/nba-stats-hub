"""
Generador del sitio estático (SEO + comparador sin backend en producción).

Usa los endpoints ya existentes de la API (GET /players, /players/{id},
/players/{id}/similar) contra un backend en marcha -- no importa nada del
backend directamente -- y genera:

- Un archivo HTML independiente por jugador en frontend/players/, con su
  detalle embebido inline (nada de fetch en tiempo de ejecución).
- frontend/players/index.html: índice de los 506 jugadores (para que Google
  pueda rastrearlos todos).
- frontend/players.json: id/nombre/slug/equipo/pts de los 506 -- lo consume
  el buscador de app.js y player.js en el navegador del usuario final.
- frontend/players-data.json (solo con --all): stats + radar + hasta 20
  comparables ya resueltos por jugador -- lo consume el comparador
  interactivo (index.html) para no depender de un backend vivo.

El backend, una vez generado esto, no necesita estar desplegado: solo se usa
en local para (re)generar datos y estas páginas cuando haga falta.

Requiere la API levantada antes de ejecutar:
    uvicorn backend.main:app --reload

Uso:
    python frontend/build_pages.py --player-id 2544        # una sola pagina (revision)
    python frontend/build_pages.py --player-name "LeBron"  # por nombre (coincidencia parcial)
    python frontend/build_pages.py --all                   # las 506 paginas + indice + JSON del comparador
"""

import argparse
import json
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

import requests

API_BASE = "http://127.0.0.1:8000"
SITE_NAME = "NBA Stats Hub"
FRONTEND_DIR = Path(__file__).resolve().parent
OUT_DIR = FRONTEND_DIR / "players"
TOP_N_SIMILAR = 10  # comparables mostrados en la propia página del jugador
MAX_SIMILAR_STATIC = 20  # cubre el maximo del selector "Nº de comparables" del comparador

# El CDN de fotos de nba.com nunca devuelve 404: si un player_id no tiene
# foto real, sirve una silueta genérica (~12 KB) con código 200 (se ve en
# la cabecera Edge-Cache-Tag: ".../fallback.png"). Por eso un <img onerror=...>
# del lado del navegador nunca se dispara -- hay que comprobar el tamaño de
# la foto en build time y decidir aquí si mostrarla o usar las iniciales.
PHOTO_SIZE_THRESHOLD_BYTES = 40_000  # fotos reales pesan 90KB+; el placeholder ~12KB


def api_get(path: str, params: dict | None = None):
    resp = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return slug or "jugador"


def build_slug_index(players: list[dict]) -> dict[int, str]:
    """player_id -> slug. Si dos nombres colisionan, el de menos partidos
    jugados recibe el sufijo -<player_id> para mantener el slug estable
    y legible para el jugador más relevante."""
    seen_slugs: set[str] = set()
    slug_by_id: dict[int, str] = {}
    for p in sorted(players, key=lambda p: -p["gp"]):
        base = slugify(p["player_name"])
        slug = base if base not in seen_slugs else f"{base}-{p['player_id']}"
        seen_slugs.add(base)
        slug_by_id[p["player_id"]] = slug
    return slug_by_id


def initials_of(name: str) -> str:
    parts = [p for p in name.split() if p]
    letters = [p[0] for p in parts if p[0].isalpha()]
    return "".join(letters[:2]).upper() or "?"


@lru_cache(maxsize=None)
def has_real_photo(player_id: int) -> bool:
    """True si el CDN de nba.com tiene una foto real (no el placeholder genérico).

    Cacheado por proceso: en --all, un jugador puede aparecer tanto como
    protagonista de su propia página como comparable de otros, y no queremos
    repetir la petición HEAD al CDN cada vez que aparece.
    """
    url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"
    try:
        resp = requests.head(url, timeout=8, allow_redirects=True)
        size = int(resp.headers.get("content-length", 0))
        return resp.status_code == 200 and size > PHOTO_SIZE_THRESHOLD_BYTES
    except requests.RequestException:
        return False


def _photo_markup(player: dict, lazy: bool, alt_text: str) -> str:
    initials = initials_of(player["player_name"])
    fallback_div = f'<div class="player-photo-fallback">{initials}</div>'
    if not has_real_photo(player["player_id"]):
        # Sin foto real conocida: mostramos las iniciales directamente,
        # sin pedir siquiera la imagen (evita una descarga inútil).
        return fallback_div.replace('class="player-photo-fallback"', 'class="player-photo-fallback" style="display:flex"')

    photo_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player['player_id']}.png"
    loading_attr = 'loading="lazy"' if lazy else 'loading="eager" fetchpriority="high"'
    # onerror se deja como red de seguridad ante fallos de red genuinos,
    # aunque la comprobación de tamaño en build time es la que de verdad filtra.
    img = (
        f'<img src="{photo_url}" alt="{alt_text}" {loading_attr} '
        "onerror=\"this.style.display='none'; this.nextElementSibling.style.display='flex';\" />"
    )
    return img + fallback_div


def player_photo_html(player: dict) -> str:
    """Foto grande de cabecera (hero): es la imagen principal de la página,
    por encima del pliegue, así que se carga eager (no lazy) para no
    penalizar el LCP."""
    return _photo_markup(player, lazy=False, alt_text=f"Foto de {player['player_name']}")


def player_avatar_thumb_html(player: dict, target_name: str) -> str:
    """Miniatura para las cards de "jugadores más parecidos": siempre lazy,
    reutiliza has_real_photo (ya cacheado, no genera peticiones nuevas para
    un jugador que ya se comprobó como target o como otro comparable)."""
    alt_text = f"Foto de {player['player_name']}, comparable de {target_name}"
    return _photo_markup(player, lazy=True, alt_text=alt_text)


def fmt(value, decimals=1):
    return "—" if value is None else f"{value:.{decimals}f}"


def fmt_pct(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def small_sample_badge_html(player: dict) -> str:
    if not player.get("small_sample"):
        return ""
    n = player["gp"]
    return (
        '<div class="small-sample-badge" '
        'title="Menos de 20 partidos o 15 minutos por partido: estas stats pueden no reflejar su nivel real">'
        f"⚠️ Muestra pequeña ({n} partido{'s' if n != 1 else ''})"
        "</div>"
    )


def stat_pill_row_html(player: dict) -> str:
    ts = player["advanced"]["ts_pct"]
    usg = player["advanced"]["usg_pct"]
    pills = [
        ("PTS", fmt(player["traditional"]["pts"])),
        ("REB", fmt(player["traditional"]["reb"])),
        ("AST", fmt(player["traditional"]["ast"])),
        ("TS%", "—" if ts is None else f"{round(ts * 100)}"),
        ("USG%", "—" if usg is None else f"{round(usg * 100)}"),
    ]
    cells = "".join(
        f'<div class="stat-pill"><div class="value">{v}</div><div class="label">{label}</div></div>'
        for label, v in pills
    )
    return f'<div class="stat-pill-row">{cells}</div>'


def stats_table_html(player: dict) -> str:
    t, p36, adv = player["traditional"], player["per36"], player["advanced"]
    rows = [
        ("Puntos (PTS)", fmt(t["pts"]), fmt(p36["pts"]), "—"),
        ("Rebotes (REB)", fmt(t["reb"]), fmt(p36["reb"]), "—"),
        ("Asistencias (AST)", fmt(t["ast"]), fmt(p36["ast"]), "—"),
        ("Robos (STL)", fmt(t["stl"]), fmt(p36["stl"]), "—"),
        ("Tapones (BLK)", fmt(t["blk"]), fmt(p36["blk"]), "—"),
        ("Pérdidas (TOV)", fmt(t["tov"]), fmt(p36["tov"]), "—"),
        ("% Tiro de campo", fmt_pct(t["fg_pct"]), "—", "—"),
        ("% Triple", fmt_pct(t["fg3_pct"]), "—", "—"),
        ("% Tiro libre", fmt_pct(t["ft_pct"]), "—", "—"),
        ("TS%", "—", "—", fmt_pct(adv["ts_pct"])),
        ("eFG%", "—", "—", fmt_pct(adv["efg_pct"])),
        ("USG%", "—", "—", fmt_pct(adv["usg_pct"])),
        ("AST%", "—", "—", fmt_pct(adv["ast_pct"])),
        ("OREB%", "—", "—", fmt_pct(adv["oreb_pct"])),
        ("DREB%", "—", "—", fmt_pct(adv["dreb_pct"])),
        ("PIE", "—", "—", fmt(adv["pie"], 3)),
    ]
    body = "".join(
        f"<tr><td>{label}</td><td>{basic}</td><td>{per36}</td><td>{advanced}</td></tr>"
        for label, basic, per36, advanced in rows
    )
    return (
        '<div class="table-scroll"><table class="stats-table">'
        f"<caption class=\"sr-only\">Estadísticas de {player['player_name']}, temporada {player['season']}"
        f" ({player['traditional']['gp']} partidos jugados)</caption>"
        "<thead><tr><th>Estadística</th><th>Básica</th><th>Per-36</th><th>Advanced</th></tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table></div>"
    )


def similar_card_html(player: dict, slug_by_id: dict[int, str], target_name: str) -> str:
    slug = slug_by_id.get(player["player_id"])
    href = f"{slug}.html" if slug else "#"
    badge = small_sample_badge_html(player)
    avatar = player_avatar_thumb_html(player, target_name)
    return f"""<a class="similar-card" href="{href}">
      <div class="similar-card-avatar">{avatar}</div>
      <div class="similar-card-body">
        <div class="similar-card-header">
          <span class="similar-card-name">{player['player_name']}</span>
          <span class="similar-card-sim">{player['similarity']}%</span>
        </div>
        <div class="similar-card-meta">{player['team_abbreviation']} · {fmt(player['traditional']['pts'])} pts / {fmt(player['traditional']['reb'])} reb / {fmt(player['traditional']['ast'])} ast</div>
        {badge}
      </div>
    </a>"""


PAGE_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<meta name="description" content="{description}" />
<link rel="stylesheet" href="../style.css" />
</head>
<body>
  <header class="topbar">
    <a class="brand" href="../index.html">
      <span class="brand-mark">NBA</span>
      <span class="brand-name">Stats Hub</span>
    </a>
    <div class="search-wrap">
      <input id="searchInput" type="text" autocomplete="off" placeholder="Busca un jugador (ej. Dončić, Gobert, Curry...)" />
      <ul id="searchResults" class="search-results" hidden></ul>
    </div>
  </header>

  <main class="player-page">
    <div class="player-hero">
      <div class="player-photo">
        {photo_html}
      </div>
      <div class="player-hero-info">
        <h1>{name}</h1>
        <div class="sub">{team} · {age} años · Temporada {season}</div>
        {badge}
        {stat_pills}
      </div>
    </div>

    <div class="panel">
      <h2>Radar comparativo</h2>
      <canvas id="radarChart"></canvas>
    </div>

    <div class="panel">
      <h2>Estadísticas</h2>
      {stats_table}
    </div>

    <div class="panel">
      <h2>Jugadores más parecidos</h2>
      <div class="similar-grid">
        {similar_cards}
      </div>
    </div>

    <p class="back-link"><a href="../index.html">&larr; Volver al comparador interactivo</a></p>
  </main>

  <script src="../vendor/chart.umd.min.js"></script>
  <script>
    window.PLAYER_RADAR = {radar_json};
    window.PLAYER_NAME = {name_json};
  </script>
  <script src="player.js"></script>
</body>
</html>
"""


def render_player_page(player: dict, similar: list[dict], slug_by_id: dict[int, str]) -> str:
    key_stats = (
        f"{fmt(player['traditional']['pts'])} PTS, {fmt(player['traditional']['ast'])} AST"
        f" y {fmt_pct(player['advanced']['ts_pct'])} de TS"
    )
    description = (
        f"{player['player_name']} ({player['team_abbreviation']}): {key_stats} en la temporada "
        f"{player['season']}. Descubre sus jugadores más parecidos por estilo de juego."
    )
    title = f"{player['player_name']} - Stats y Jugadores Similares | {SITE_NAME}"

    return PAGE_TEMPLATE.format(
        title=title,
        description=description,
        photo_html=player_photo_html(player),
        name=player["player_name"],
        team=player["team_abbreviation"],
        age=int(player["age"]) if player["age"] is not None else "?",
        season=player["season"],
        badge=small_sample_badge_html(player),
        stat_pills=stat_pill_row_html(player),
        stats_table=stats_table_html(player),
        similar_cards="".join(similar_card_html(s, slug_by_id, player["player_name"]) for s in similar),
        radar_json=json.dumps(player["radar"], ensure_ascii=False),
        name_json=json.dumps(player["player_name"], ensure_ascii=False),
    )


INDEX_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Todos los jugadores | {site_name}</title>
<meta name="description" content="Índice completo de jugadores NBA con stats y comparador de similitud, temporada {season}." />
<link rel="stylesheet" href="../style.css" />
</head>
<body>
  <header class="topbar">
    <a class="brand" href="../index.html">
      <span class="brand-mark">NBA</span>
      <span class="brand-name">Stats Hub</span>
    </a>
    <div class="search-wrap">
      <input id="searchInput" type="text" autocomplete="off" placeholder="Busca un jugador (ej. Dončić, Gobert, Curry...)" />
      <ul id="searchResults" class="search-results" hidden></ul>
    </div>
  </header>

  <main class="player-page">
    <div class="panel">
      <h2>Todos los jugadores ({count})</h2>
      <input id="indexFilter" class="index-filter" type="text" autocomplete="off"
             placeholder="Filtrar por nombre en esta lista..." />
      <nav class="index-nav">
        {letter_nav}
      </nav>
      {letter_groups}
      <p id="indexNoMatches" class="hint" hidden>Ningún jugador coincide con el filtro.</p>
    </div>
    <p class="back-link"><a href="../index.html">&larr; Volver al comparador interactivo</a></p>
  </main>

  <script src="../vendor/chart.umd.min.js"></script>
  <script>window.PLAYER_RADAR = null;</script>
  <script src="player.js"></script>
</body>
</html>
"""


def _normalize_for_search(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def render_index_page(players: list[dict], slug_by_id: dict[int, str], season: str) -> str:
    existing_slugs = {p.stem for p in OUT_DIR.glob("*.html") if p.stem != "index"}

    groups: dict[str, list[dict]] = {}
    for p in sorted(players, key=lambda p: p["player_name"]):
        first = p["player_name"][0].upper()
        letter = first if first.isalpha() else "#"
        groups.setdefault(letter, []).append(p)

    letter_nav = "\n        ".join(f'<a href="#letter-{letter}">{letter}</a>' for letter in groups)

    letter_groups_html = []
    for letter, group_players in groups.items():
        rows = []
        for p in group_players:
            slug = slug_by_id[p["player_id"]]
            team = f'<span class="team">{p["team_abbreviation"]}</span>'
            search_key = _normalize_for_search(p["player_name"])
            if slug in existing_slugs:
                rows.append(
                    f'<li data-name="{search_key}"><a href="{slug}.html">{p["player_name"]}</a>{team}</li>'
                )
            else:
                rows.append(f'<li data-name="{search_key}" class="pending">{p["player_name"]}{team}</li>')
        letter_groups_html.append(
            f'<section class="index-letter-group" id="letter-{letter}">'
            f"<h3>{letter}</h3>"
            f'<ul class="player-index-list">{"".join(rows)}</ul>'
            "</section>"
        )

    return INDEX_TEMPLATE.format(
        site_name=SITE_NAME,
        season=season,
        count=len(players),
        letter_nav=letter_nav,
        letter_groups="\n      ".join(letter_groups_html),
    )


def fetch_player_data(player_id: int) -> tuple[dict, list[dict]]:
    """Un único par de llamadas a la API por jugador: el detalle y hasta
    MAX_SIMILAR_STATIC comparables. Se reutiliza tanto para la página estática
    individual (que solo muestra TOP_N_SIMILAR) como para el registro que
    alimenta el comparador interactivo (players-data.json)."""
    detail = api_get(f"/players/{player_id}")
    similar_resp = api_get(f"/players/{player_id}/similar?n={MAX_SIMILAR_STATIC}")
    return detail, similar_resp["similar"]


def write_player_page(detail: dict, similar_full: list[dict], slug_by_id: dict[int, str]) -> str:
    slug = slug_by_id[detail["player_id"]]
    html = render_player_page(detail, similar_full[:TOP_N_SIMILAR], slug_by_id)
    (OUT_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
    return slug


def comparator_record(detail: dict, similar_full: list[dict]) -> dict:
    """Registro reducido para players-data.json: solo lo que el comparador
    interactivo (app.js) necesita para pintar la tarjeta, la lista y el radar
    de cualquiera de los 506 jugadores sin backend."""
    return {
        "player_id": detail["player_id"],
        "player_name": detail["player_name"],
        "team_abbreviation": detail["team_abbreviation"],
        "age": detail["age"],
        "season": detail["season"],
        "gp": detail["gp"],
        "small_sample": detail["small_sample"],
        "traditional": {
            "pts": detail["traditional"]["pts"],
            "reb": detail["traditional"]["reb"],
            "ast": detail["traditional"]["ast"],
        },
        "advanced": {
            "ts_pct": detail["advanced"]["ts_pct"],
            "usg_pct": detail["advanced"]["usg_pct"],
        },
        "radar": detail["radar"],
        "similar": [{"player_id": s["player_id"], "similarity": s["similarity"]} for s in similar_full],
    }


def main():
    parser = argparse.ArgumentParser(description="Genera páginas estáticas de jugador para SEO")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--player-id", type=int, help="Genera solo este player_id")
    group.add_argument("--player-name", type=str, help="Genera el primer jugador cuyo nombre contenga esto")
    group.add_argument("--all", action="store_true", help="Genera las páginas de todos los jugadores + índice")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        players = api_get("/players")
    except requests.RequestException as exc:
        print(f"ERROR: no se pudo conectar a la API en {API_BASE}. ¿Está arrancado uvicorn? ({exc})")
        sys.exit(1)

    slug_by_id = build_slug_index(players)
    season = api_get(f"/players/{players[0]['player_id']}")["season"] if players else "2025-26"

    # players.json: índice ligero para el buscador (comparador + páginas de
    # jugador). Barato -- ya tenemos /players en memoria, sin llamadas extra.
    search_index = [
        {
            "id": p["player_id"],
            "name": p["player_name"],
            "slug": slug_by_id[p["player_id"]],
            "team": p["team_abbreviation"],
            "pts": p["pts"],
        }
        for p in players
    ]
    (FRONTEND_DIR / "players.json").write_text(json.dumps(search_index, ensure_ascii=False), encoding="utf-8")
    print(f"Generado: frontend/players.json ({len(search_index)} jugadores)")

    # Ya no se usa: sustituido por players.json (que ya incluye el slug).
    stale_index = OUT_DIR / "players-index.json"
    if stale_index.exists():
        stale_index.unlink()

    if args.player_id:
        detail, similar_full = fetch_player_data(args.player_id)
        slug = write_player_page(detail, similar_full, slug_by_id)
        print(f"Generado: frontend/players/{slug}.html")
    elif args.player_name:
        # Reutiliza la búsqueda real de la API (tolerante a acentos: "doncic"
        # encuentra a "Dončić") en vez de un substring plano, para que este
        # comando encuentre lo mismo que encontraría un usuario en la web.
        matches = api_get("/players", params={"q": args.player_name})
        if not matches:
            print(f"No se encontró ningún jugador que contenga '{args.player_name}'.")
            sys.exit(1)
        detail, similar_full = fetch_player_data(matches[0]["player_id"])
        slug = write_player_page(detail, similar_full, slug_by_id)
        print(f"Generado: frontend/players/{slug}.html  ({detail['player_name']})")
    elif args.all:
        # players-data.json (dataset del comparador interactivo) solo tiene
        # sentido generarlo completo -- necesita los 506 jugadores para que
        # cualquier búsqueda funcione, así que se acumula aquí mismo,
        # reutilizando las llamadas que ya hacíamos para las páginas
        # individuales (sin peticiones adicionales a la API).
        comparator_data = {}
        for i, p in enumerate(players, start=1):
            detail, similar_full = fetch_player_data(p["player_id"])
            slug = write_player_page(detail, similar_full, slug_by_id)
            comparator_data[str(p["player_id"])] = comparator_record(detail, similar_full)
            print(f"[{i}/{len(players)}] {slug}.html")

        (FRONTEND_DIR / "players-data.json").write_text(
            json.dumps(comparator_data, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Generado: frontend/players-data.json ({len(comparator_data)} jugadores)")

    # El índice se regenera siempre: refleja qué páginas existen ya en disco.
    index_html = render_index_page(players, slug_by_id, season)
    (OUT_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"Índice actualizado: frontend/players/index.html ({len(players)} jugadores listados)")


if __name__ == "__main__":
    main()
