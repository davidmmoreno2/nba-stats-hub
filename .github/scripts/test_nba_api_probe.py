"""
Prueba minima de conectividad: ¿bloquea stats.nba.com las peticiones desde
las IPs de los runners de GitHub Actions?

Sin reintentos ni backoff a proposito -- queremos ver el comportamiento
"en crudo" (bloqueo, timeout, 403, etc.) tal cual, no enmascarado por
nuestra propia logica de reintento (esa vive en data/fetch_stats.py).
"""

import sys

from nba_api.stats.endpoints import commonplayerinfo

LEBRON_JAMES_ID = 2544

try:
    resp = commonplayerinfo.CommonPlayerInfo(player_id=LEBRON_JAMES_ID, timeout=30)
    df = resp.get_data_frames()[0]
    row = df.iloc[0]
    print(f"OK: {row['DISPLAY_FIRST_LAST']} - {row['TEAM_NAME']} ({row['TEAM_ABBREVIATION']})")
except Exception as exc:
    print(f"FALLO: {type(exc).__name__}: {exc}")
    sys.exit(1)
