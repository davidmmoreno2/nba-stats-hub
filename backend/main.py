"""
API FastAPI de NBA Stats Hub.

Arranque local:
    uvicorn backend.main:app --reload
"""

import unicodedata

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.similarity import MIN_GP_CANDIDATE, MIN_MPG_CANDIDATE, get_similarity_engine

app = FastAPI(title="NBA Stats Hub API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # uso local; restringir si se despliega en público
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sf(value) -> float | None:
    """Convierte a float nativo de Python, o None si es NaN (para JSON)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def _normalize_text(s: str) -> str:
    """Quita acentos y pasa a minúsculas, para búsquedas tolerantes (Dončić -> doncic)."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _player_summary(row) -> dict:
    gp = int(row["gp"])
    minutes = _sf(row["min"]) or 0.0
    return {
        "player_id": int(row["player_id"]),
        "player_name": row["player_name"],
        "team_abbreviation": row["team_abbreviation"],
        "age": _sf(row["age"]),
        "gp": gp,
        "min": _sf(row["min"]),
        "pts": _sf(row["pts"]),
        "reb": _sf(row["reb"]),
        "ast": _sf(row["ast"]),
        "ts_pct": _sf(row["ts_pct"]),
        "usg_pct": _sf(row["usg_pct"]),
        # Mismo umbral que el pool de comparables fiables (backend/similarity.py).
        # No afecta a quién se puede buscar/comparar, solo marca el dato en la UI.
        "small_sample": gp < MIN_GP_CANDIDATE or minutes < MIN_MPG_CANDIDATE,
    }


def _player_detail(row, engine) -> dict:
    detail = _player_summary(row)
    detail.update(
        {
            "season": row["season"],
            "traditional": {
                "gp": int(row["gp"]),
                "min": _sf(row["min"]),
                "pts": _sf(row["pts"]),
                "reb": _sf(row["reb"]),
                "ast": _sf(row["ast"]),
                "stl": _sf(row["stl"]),
                "blk": _sf(row["blk"]),
                "tov": _sf(row["tov"]),
                "fg_pct": _sf(row["fg_pct_x"]),
                "fg3_pct": _sf(row["fg3_pct"]),
                "ft_pct": _sf(row["ft_pct"]),
            },
            "per36": {
                "pts": _sf(row["per36_pts"]),
                "reb": _sf(row["per36_reb"]),
                "ast": _sf(row["per36_ast"]),
                "stl": _sf(row["per36_stl"]),
                "blk": _sf(row["per36_blk"]),
                "tov": _sf(row["per36_tov"]),
            },
            "advanced": {
                "ts_pct": _sf(row["ts_pct"]),
                "efg_pct": _sf(row["efg_pct"]),
                "usg_pct": _sf(row["usg_pct"]),
                "ast_pct": _sf(row["ast_pct"]),
                "oreb_pct": _sf(row["oreb_pct"]),
                "dreb_pct": _sf(row["dreb_pct"]),
                "off_rating": _sf(row["off_rating"]),
                "def_rating": _sf(row["def_rating"]),
                "pie": _sf(row["pie"]),
            },
            "shot_profile": {
                "pct_fga_3pt": _sf(row["pct_fga_3pt"]),
                "pct_pts_2pt_mr": _sf(row["pct_pts_2pt_mr"]),
                "pct_pts_paint": _sf(row["pct_pts_paint"]),
                "pct_pts_fb": _sf(row["pct_pts_fb"]),
                "pct_ast_3pm": _sf(row["pct_ast_3pm"]),
                "pct_uast_fgm": _sf(row["pct_uast_fgm"]),
            },
            "radar": engine.get_radar_profile(int(row["player_id"])),
        }
    )
    return detail


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/players")
def list_players(q: str | None = Query(None, description="Filtro por nombre (sin distinguir acentos)"),
                  season: str | None = None):
    try:
        engine = get_similarity_engine(season)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    df = engine.df
    if q:
        q_norm = _normalize_text(q)
        mask = df["player_name"].apply(lambda name: q_norm in _normalize_text(name))
        df = df[mask]

    df = df.sort_values("pts", ascending=False)
    return [_player_summary(row) for _, row in df.iterrows()]


@app.get("/players/{player_id}")
def player_detail(player_id: int, season: str | None = None):
    try:
        engine = get_similarity_engine(season)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not engine.has_player(player_id):
        raise HTTPException(status_code=404, detail=f"Jugador {player_id} no encontrado")

    row = engine.df[engine.df["player_id"] == player_id].iloc[0]
    return _player_detail(row, engine)


@app.get("/players/{player_id}/similar")
def similar_players(
    player_id: int,
    n: int = Query(10, ge=1, le=30),
    season: str | None = None,
):
    try:
        engine = get_similarity_engine(season)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not engine.has_player(player_id):
        raise HTTPException(status_code=404, detail=f"Jugador {player_id} no encontrado")

    target_row = engine.df[engine.df["player_id"] == player_id].iloc[0]
    target = _player_detail(target_row, engine)

    similar = []
    for match in engine.get_similar_players(player_id, n=n):
        match_row = engine.df[engine.df["player_id"] == match["player_id"]].iloc[0]
        match_detail = _player_detail(match_row, engine)
        match_detail["similarity"] = match["similarity"]
        similar.append(match_detail)

    return {"target": target, "similar": similar}
