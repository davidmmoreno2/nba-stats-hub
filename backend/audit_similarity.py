"""
Auditoría manual del motor de similitud: comprueba que los comparables de
jugadores con perfiles muy distintos tienen sentido baloncedistico.

No modifica el modelo -- solo lo ejecuta y muestra resultados para revisión.

Uso:
    python backend/audit_similarity.py
"""

import unicodedata

from backend.similarity import FEATURE_COLUMNS, get_similarity_engine

# Un jugador de cada arquetipo, elegido para maximizar el contraste de estilo.
TARGET_PLAYERS = [
    ("Rudy Gobert", "Center puro (protector de aro, cero tiro exterior)"),
    ("Chris Paul", "Base organizador clasico (poco volumen, ojo: solo 16 GP en 25-26)"),
    ("Klay Thompson", "Especialista en triples (bajo uso, altísimo volumen de 3PA)"),
    ("LeBron James", "Superestrella todoterreno (alto uso, anota + crea + rebota)"),
    ("Herbert Jones", "Rol player defensivo (bajo uso, ala-pivot/alero 3&D)"),
]

# Subconjunto de FEATURE_COLUMNS + identidad, pensado para lectura humana.
DISPLAY_STATS = [
    ("usg_pct", "USG%", "pct"),
    ("ts_pct", "TS%", "pct"),
    ("ast_pct", "AST%", "pct"),
    ("pct_fga_3pt", "3PA rate", "pct"),
    ("oreb_pct", "OREB%", "pct"),
    ("dreb_pct", "DREB%", "pct"),
    ("per36_pts", "PTS/36", "num"),
    ("per36_ast", "AST/36", "num"),
    ("per36_stl", "STL/36", "num"),
    ("per36_blk", "BLK/36", "num"),
]


def normalize(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def fmt_stat(row, col, kind):
    val = row[col]
    if val is None:
        return "  n/a"
    if kind == "pct":
        return f"{val * 100:5.1f}%"
    return f"{val:5.1f}"


def print_player_line(row, label_width=28, similarity=None):
    label = f"{row['player_name']} ({row['team_abbreviation']})"
    stats = "  ".join(f"{name}={fmt_stat(row, col, kind)}" for col, name, kind in DISPLAY_STATS)
    sim = f"  sim={similarity:5.1f}%" if similarity is not None else ""
    print(f"  {label:<{label_width}}{sim}  GP={int(row['gp']):>3}  MIN={row['min']:4.1f}  {stats}")


def find_player(engine, name):
    target_norm = normalize(name)
    match = engine.df[engine.df["player_name"].apply(lambda n: normalize(n) == target_norm)]
    if match.empty:
        match = engine.df[engine.df["player_name"].apply(lambda n: target_norm in normalize(n))]
    return match.iloc[0] if not match.empty else None


def main():
    engine = get_similarity_engine()
    print(f"Motor cargado: {len(engine)} jugadores, temporada {engine.df['season'].iloc[0]}")
    print(f"Features usadas para similitud ({len(FEATURE_COLUMNS)}): {', '.join(FEATURE_COLUMNS)}")
    print("NOTA: no hay columna de posicion/rol en los datos -- no se puede mostrar ni filtrar por ella.\n")

    for name, archetype in TARGET_PLAYERS:
        row = find_player(engine, name)
        print("=" * 100)
        if row is None:
            print(f"[{name}] NO ENCONTRADO en la temporada cargada (¿menos de 10 GP o nombre distinto?)")
            continue

        print(f"[{archetype}]")
        print_player_line(row)
        print("-" * 100)

        try:
            similar = engine.get_similar_players(int(row["player_id"]), n=10)
        except KeyError as exc:
            print(f"  ERROR: {exc}")
            continue

        for match in similar:
            match_row = engine.df[engine.df["player_id"] == match["player_id"]].iloc[0]
            print_player_line(match_row, similarity=match["similarity"])
        print()


if __name__ == "__main__":
    main()
