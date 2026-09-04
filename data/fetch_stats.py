"""
Descarga stats de jugadores NBA (básicas, per-36 y advanced/scoring) via nba_api
y las guarda en una base de datos SQLite local (data/nba_stats.db).

Uso:
    python data/fetch_stats.py
    python data/fetch_stats.py --season 2024-25
    python data/fetch_stats.py --season 2025-26 --season-type "Playoffs"

La web NUNCA llama a la API de la NBA en caliente: siempre lee de esta
base de datos. Vuelve a ejecutar este script cuando quieras refrescar los datos.
"""

import argparse
import random
import sys
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "nba_stats.db"

REQUEST_TIMEOUT = 60          # segundos, stats.nba.com puede ser lento
MAX_RETRIES = 4
BASE_BACKOFF = 3.0            # segundos, crece exponencialmente entre reintentos
DELAY_BETWEEN_CALLS = (1.5, 3.0)  # rango de espera aleatoria entre llamadas


def log(msg: str) -> None:
    print(f"[fetch_stats] {msg}", flush=True)


def fetch_with_retry(measure_type: str, per_mode: str, season: str, season_type: str) -> pd.DataFrame:
    """Llama a LeagueDashPlayerStats con reintentos y backoff exponencial."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f"  -> {measure_type} / {per_mode} (intento {attempt}/{MAX_RETRIES})")
            resp = leaguedashplayerstats.LeagueDashPlayerStats(
                season=season,
                season_type_all_star=season_type,
                measure_type_detailed_defense=measure_type,
                per_mode_detailed=per_mode,
                timeout=REQUEST_TIMEOUT,
            )
            df = resp.get_data_frames()[0]
            if df.empty:
                raise ValueError("Respuesta vacía de la API")
            return df
        except Exception as exc:  # nba_api puede lanzar varios tipos (timeout, JSON, HTTP)
            last_error = exc
            wait = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 1.5)
            log(f"     error: {exc!r} -- reintentando en {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Fallaron todos los reintentos para {measure_type}/{per_mode}") from last_error


def polite_sleep() -> None:
    time.sleep(random.uniform(*DELAY_BETWEEN_CALLS))


def snake_case_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    return df


def dedupe_traded_players(df: pd.DataFrame) -> pd.DataFrame:
    """
    LeagueDashPlayerStats devuelve una fila por equipo si el jugador fue
    traspaseado (más una fila TOT con el acumulado). Nos quedamos con la fila
    de mayor GP por jugador (normalmente la TOT), para no duplicar jugadores.
    """
    if df["player_id"].duplicated().any():
        df = df.sort_values("gp", ascending=False).drop_duplicates("player_id", keep="first")
    return df.reset_index(drop=True)


def build_player_stats(season: str, season_type: str) -> pd.DataFrame:
    log(f"Descargando temporada {season} ({season_type})...")

    base = fetch_with_retry("Base", "PerGame", season, season_type)
    polite_sleep()
    per36 = fetch_with_retry("Base", "Per36", season, season_type)
    polite_sleep()
    advanced = fetch_with_retry("Advanced", "PerGame", season, season_type)
    polite_sleep()
    scoring = fetch_with_retry("Scoring", "PerGame", season, season_type)

    base = dedupe_traded_players(snake_case_columns(base))
    per36 = dedupe_traded_players(snake_case_columns(per36))
    advanced = dedupe_traded_players(snake_case_columns(advanced))
    scoring = dedupe_traded_players(snake_case_columns(scoring))

    # Columnas de identidad que se repiten en cada endpoint; solo nos
    # interesa mantenerlas una vez (las de "base").
    identity_cols = {
        "player_id", "player_name", "nickname", "team_id", "team_abbreviation",
        "age", "gp", "gp_rank", "w", "l", "w_pct", "min",
    }

    per36_stats = per36.drop(columns=[c for c in identity_cols if c in per36.columns and c != "player_id"])
    per36_stats = per36_stats.rename(
        columns={c: f"per36_{c}" for c in per36_stats.columns if c != "player_id"}
    )

    advanced_stats = advanced.drop(columns=[c for c in identity_cols if c in advanced.columns and c != "player_id"])

    scoring_stats = scoring.drop(columns=[c for c in identity_cols if c in scoring.columns and c != "player_id"])

    merged = base.merge(per36_stats, on="player_id", how="left")
    merged = merged.merge(advanced_stats, on="player_id", how="left")
    merged = merged.merge(scoring_stats, on="player_id", how="left")

    merged["season"] = season
    merged["season_type"] = season_type
    merged["fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()

    log(f"Total jugadores: {len(merged)} | columnas: {len(merged.columns)}")
    return merged


def save_to_sqlite(df: pd.DataFrame, season: str, season_type: str) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH}")

    with engine.begin() as conn:
        # Creamos la tabla si no existe (a partir del propio dataframe) y
        # luego sustituimos solo las filas de esta temporada/season_type,
        # para poder acumular varias temporadas sin perder las anteriores.
        df.head(0).to_sql("player_stats", conn, if_exists="append", index=False)
        conn.execute(
            text("DELETE FROM player_stats WHERE season = :season AND season_type = :season_type"),
            {"season": season, "season_type": season_type},
        )

    df.to_sql("player_stats", engine, if_exists="append", index=False)
    log(f"Guardado en {DB_PATH} (tabla player_stats)")


def parse_args():
    parser = argparse.ArgumentParser(description="Descarga stats NBA a SQLite local")
    parser.add_argument("--season", default=None, help='Ej: "2025-26". Por defecto, la temporada actual/última disponible.')
    parser.add_argument("--season-type", default="Regular Season", choices=["Regular Season", "Playoffs", "Pre Season"])
    return parser.parse_args()


def main():
    args = parse_args()

    # Temporada actual/última disponible si no se especifica una.
    season = args.season or "2025-26"

    try:
        df = build_player_stats(season, args.season_type)
    except RuntimeError as exc:
        log(f"ERROR: no se pudieron descargar los datos: {exc}")
        sys.exit(1)

    save_to_sqlite(df, season, args.season_type)
    log("Listo.")


if __name__ == "__main__":
    main()
