# NBA Stats Hub

Sitio local de estadísticas avanzadas de la NBA con motor de comparación/similitud de jugadores.

## Estructura

```
nba-stats-hub/
├── venv/                 # entorno virtual de Python (no versionado)
├── data/                 # scripts de extracción (nba_api) y cache local (SQLite)
├── backend/              # API (FastAPI) + motor de similitud (scikit-learn)
├── frontend/             # interfaz web
└── requirements.txt
```

## Puesta en marcha

1. Activar el entorno virtual (PowerShell): `venv\Scripts\Activate.ps1`
2. Instalar dependencias (ya hecho si sigues estos pasos por primera vez): `pip install -r requirements.txt`
3. Descargar/actualizar datos: `python data/fetch_stats.py` (tarda ~1 min, respeta el rate limit de stats.nba.com)
4. Levantar la API: `uvicorn backend.main:app --reload` → http://127.0.0.1:8000
5. En otra terminal, servir el frontend: `python -m http.server 5500 --directory frontend` → http://localhost:5500
6. Abrir http://localhost:5500 y buscar un jugador (ej. "Dončić", "Gobert", "Curry")

El frontend es HTML/CSS/JS puro (sin build step) y usa una copia local de Chart.js
en `frontend/vendor/` para no depender de conexión a internet.

Notas del motor de similitud:
- Solo se comparan jugadores con 10+ partidos jugados (evita ruido de muestra pequeña).
- La similitud se basa en 18 métricas de *estilo de juego* (uso, eficiencia,
  creación, rebote, defensa/actividad y perfil de tiro) normalizadas con
  `StandardScaler`, no en volumen bruto de PTS/REB/AST.
