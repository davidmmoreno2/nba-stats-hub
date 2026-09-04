"""
Motor de similitud de jugadores.

Normaliza un conjunto curado de estadísticas (StandardScaler) y calcula
similitud entre jugadores con k-NN (scikit-learn, distancia coseno).

Dos correcciones introducidas tras la auditoría manual (ver audit_similarity.py):

1. Pool de búsqueda vs. pool de comparables: cualquier jugador con GP >= MIN_GP_SEARCH
   se puede buscar y ver en detalle, pero solo los jugadores con muestra fiable
   (GP >= MIN_GP_CANDIDATE y MIN >= MIN_MPG_CANDIDATE) pueden aparecer como
   comparable de otro. Así un jugador de muestra pequeña (ej. una lesión a
   mitad de temporada) se puede seguir consultando, pero no contamina los
   resultados de similitud de los demás con stats ruidosas.
2. Ponderación por categoría, no por columna: FEATURE_COLUMNS agrupa varias
   columnas correlacionadas bajo un mismo concepto (ej. TS% y eFG% miden casi
   lo mismo). Sin corrección, una categoría con más columnas correlacionadas
   pesa más en la distancia coseno solo por tener más columnas. Cada feature
   se reescala por 1/sqrt(tamaño de su categoría) para que las ~5 categorías
   de FEATURE_GROUPS aporten, en conjunto, el mismo peso a la similitud.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from backend.database import get_engine

# Estadísticas usadas para medir "estilo de juego" (no volumen bruto):
# volumen/eficiencia anotadora, creación de juego, rebote, defensa/actividad
# y perfil de tiro. Se excluyen deliberadamente PTS/REB/AST totales para que
# la similitud no dependa solo de minutos jugados.
#
# Agrupadas por categoría conceptual (mismo espíritu que RADAR_AXES, pero
# cubriendo las 18 columnas): esto es lo que se usa para ponderar cada
# categoría por igual en get_similar_players, independientemente de cuántas
# columnas correlacionadas tenga dentro.
FEATURE_GROUPS = {
    "anotacion_eficiencia": ["usg_pct", "per36_pts", "ts_pct", "efg_pct"],
    "creacion": ["per36_ast", "ast_pct", "ast_to", "per36_tov"],
    "rebote": ["oreb_pct", "dreb_pct"],
    "defensa": ["per36_stl", "per36_blk"],
    "perfil_tiro": [
        "pct_fga_3pt",
        "pct_pts_2pt_mr",
        "pct_pts_paint",
        "pct_pts_fb",
        "pct_ast_3pm",
        "pct_uast_fgm",
    ],
}

FEATURE_COLUMNS = [col for cols in FEATURE_GROUPS.values() for col in cols]

# Peso de cada feature = 1/sqrt(nº columnas de su categoría). Con esto, la
# suma de varianza que aporta cada categoría a la distancia coseno es
# aproximadamente la misma sin importar si la respaldan 2 o 6 columnas.
_GROUP_OF_FEATURE = {col: group for group, cols in FEATURE_GROUPS.items() for col in cols}
FEATURE_WEIGHTS = np.array(
    [1.0 / np.sqrt(len(FEATURE_GROUPS[_GROUP_OF_FEATURE[col]])) for col in FEATURE_COLUMNS]
)

MIN_GP_SEARCH = 10  # umbral para poder buscar/ver a un jugador (sin cambios)

# Umbral para poder aparecer como comparable de otro jugador. Más estricto
# que MIN_GP_SEARCH: la auditoría mostró jugadores de 9-13 minutos o ~12
# partidos colándose como top comparable solo por ruido estadístico.
MIN_GP_CANDIDATE = 20
MIN_MPG_CANDIDATE = 15.0

# Ejes agregados (percentil 0-100 dentro de la temporada) para el radar chart
# del frontend. Cada eje promedia el percentil de una o varias FEATURE_COLUMNS.
RADAR_AXES = {
    "Anotación": ["per36_pts"],
    "Eficiencia": ["ts_pct"],
    "Creación": ["per36_ast", "ast_pct"],
    "Rebote": ["oreb_pct", "dreb_pct"],
    "Defensa": ["per36_stl", "per36_blk"],
    "Tiro exterior": ["pct_fga_3pt"],
}


def load_player_stats(season: str | None = None, season_type: str = "Regular Season") -> pd.DataFrame:
    """Carga de SQLite la temporada indicada (o la más reciente disponible)."""
    engine = get_engine()

    if season is None:
        row = pd.read_sql(
            "SELECT MAX(season) AS season FROM player_stats WHERE season_type = %(st)s"
            if engine.dialect.name != "sqlite"
            else "SELECT MAX(season) AS season FROM player_stats WHERE season_type = :st",
            engine,
            params={"st": season_type},
        )
        season = row["season"].iloc[0]
        if season is None:
            raise ValueError("No hay datos en la base de datos. Ejecuta data/fetch_stats.py primero.")

    df = pd.read_sql(
        "SELECT * FROM player_stats WHERE season = :season AND season_type = :season_type",
        engine,
        params={"season": season, "season_type": season_type},
    )
    df = df[df["gp"] >= MIN_GP_SEARCH].reset_index(drop=True)
    return df


@dataclass
class PlayerSimilarityEngine:
    df: pd.DataFrame
    feature_columns: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))

    def __post_init__(self):
        self.df = self.df.reset_index(drop=True)

        raw_features = self.df[self.feature_columns].astype(float)

        # Pool de candidatos: solo estos jugadores pueden salir como
        # comparable de otro (ver MIN_GP_CANDIDATE / MIN_MPG_CANDIDATE arriba).
        # Cualquier jugador de self.df se puede seguir buscando y consultando
        # aunque no sea candidato (ej. alguien con pocos partidos por lesión).
        candidate_mask = (self.df["gp"] >= MIN_GP_CANDIDATE) & (self.df["min"] >= MIN_MPG_CANDIDATE)
        self.candidate_idx = np.flatnonzero(candidate_mask.to_numpy())
        if len(self.candidate_idx) == 0:
            raise ValueError("Ningún jugador cumple el umbral de candidato (GP/MIN mínimos).")

        # Imputer y scaler se ajustan SOLO con el pool fiable, para que la
        # noción de "rango normal" no la distorsionen los jugadores de
        # muestra pequeña; luego se aplican a todo el pool buscable.
        candidate_features = raw_features.iloc[self.candidate_idx]
        imputer = SimpleImputer(strategy="median").fit(candidate_features)
        imputed = imputer.transform(raw_features)

        scaler = StandardScaler().fit(imputer.transform(candidate_features))
        self.X = scaler.transform(imputed) * FEATURE_WEIGHTS

        # Percentil (0-100) de cada feature dentro del pool buscable, usado
        # para construir perfiles comparables en el radar chart.
        self.percentiles = pd.DataFrame(imputed, columns=self.feature_columns).rank(pct=True) * 100

        n_neighbors = min(len(self.candidate_idx), 50)
        self.model = NearestNeighbors(metric="cosine", n_neighbors=n_neighbors)
        self.model.fit(self.X[self.candidate_idx])

        self._id_to_idx = {int(pid): i for i, pid in enumerate(self.df["player_id"])}

    def __len__(self):
        return len(self.df)

    def has_player(self, player_id: int) -> bool:
        return player_id in self._id_to_idx

    def get_similar_players(self, player_id: int, n: int = 10) -> list[dict]:
        idx = self._id_to_idx.get(player_id)
        if idx is None:
            raise KeyError(f"player_id {player_id} no encontrado en la temporada cargada")

        # Se consulta contra el índice de candidatos (fiables) aunque el
        # propio jugador buscado no sea uno de ellos.
        n_query = min(n + 1, len(self.candidate_idx))
        distances, indices = self.model.kneighbors(self.X[idx : idx + 1], n_neighbors=n_query)

        results = []
        for dist, local_i in zip(distances[0], indices[0]):
            global_i = int(self.candidate_idx[local_i])
            if global_i == idx:
                continue
            similarity_pct = round(max(0.0, 1 - dist) * 100, 1)  # coseno: 1 - distancia
            row = self.df.iloc[global_i]
            results.append(
                {
                    "player_id": int(row["player_id"]),
                    "player_name": row["player_name"],
                    "team_abbreviation": row["team_abbreviation"],
                    "similarity": similarity_pct,
                }
            )

        results.sort(key=lambda r: r["similarity"], reverse=True)
        return results[:n]

    def get_radar_profile(self, player_id: int) -> dict[str, float]:
        idx = self._id_to_idx.get(player_id)
        if idx is None:
            raise KeyError(f"player_id {player_id} no encontrado en la temporada cargada")
        row_pct = self.percentiles.iloc[idx]
        return {label: round(float(row_pct[cols].mean()), 1) for label, cols in RADAR_AXES.items()}


_engine_cache: dict[tuple, PlayerSimilarityEngine] = {}


def get_similarity_engine(season: str | None = None, season_type: str = "Regular Season") -> PlayerSimilarityEngine:
    """Devuelve un motor de similitud cacheado en memoria por (temporada, tipo)."""
    df = load_player_stats(season, season_type)
    resolved_season = df["season"].iloc[0]
    cache_key = (resolved_season, season_type)

    if cache_key not in _engine_cache:
        _engine_cache[cache_key] = PlayerSimilarityEngine(df)
    return _engine_cache[cache_key]


if __name__ == "__main__":
    import sys

    engine = get_similarity_engine()
    print(f"Motor cargado con {len(engine)} jugadores.")

    name_query = sys.argv[1] if len(sys.argv) > 1 else "Luka"
    match = engine.df[engine.df["player_name"].str.contains(name_query, case=False, na=False)]
    if match.empty:
        print(f"No se encontró ningún jugador que contenga '{name_query}'.")
        sys.exit(0)

    target = match.iloc[0]
    print(f"\nJugadores más parecidos a {target['player_name']} ({target['team_abbreviation']}):")
    for r in engine.get_similar_players(int(target["player_id"]), n=10):
        print(f"  {r['similarity']:5.1f}%  {r['player_name']} ({r['team_abbreviation']})")
