"""Acceso a la base de datos SQLite generada por data/fetch_stats.py."""

from pathlib import Path

from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "nba_stats.db"

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"No existe {DB_PATH}. Ejecuta primero: python data/fetch_stats.py"
            )
        _engine = create_engine(f"sqlite:///{DB_PATH}")
    return _engine
