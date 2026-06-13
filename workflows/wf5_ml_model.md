# WF5 — ML Predictive Model: Plan Detallado

## Objetivo
Reemplazar el modelo de scoring ponderado (heurístico) con un modelo de ML entrenado en datos históricos reales. El modelo debe predecir probabilidades de resultado (W/D/L) y líneas de mercados secundarios (corners, tarjetas) para cada partido del WC 2026.

---

## Por qué el modelo actual es insuficiente

| Problema | Impacto |
|----------|---------|
| Form W/D/L sin contexto de rival | Irán 10W en AFC = Bélgica 7W en UEFA (falso) |
| Pesos fijados a mano (W_WINNER) | No hay aprendizaje de errores pasados |
| Sin historial de enfrentamientos directos | H2H = 0.5 fijo siempre |
| Sin datos de posesión, disparos, xG | Modelo ciego a estilo de juego |
| Sin calibración post-resultado | No mejora con el torneo en curso |

---

## Arquitectura del modelo ML

### Enfoque recomendado: Dixon-Coles (Poisson bivariada)
- Predice **goles esperados** por equipo (λ_home, λ_away)
- De λ → distribución sobre (goles_home, goles_away) → probabilidades 1X2
- Estándar en football analytics, funciona bien con datos limitados
- Interpretable: cada feature tiene coeficiente claro

Alternativa si hay suficiente data: **XGBoost con calibración Platt**.

---

## Features del modelo

### Por equipo (extraídas de api-sports.io + StatsBomb)

| Feature | Fuente | Disponible hoy |
|---------|--------|---------------|
| Goles marcados/partido (adj. calidad oponente) | api-sports.io | ✅ |
| Goles recibidos/partido (adj. calidad oponente) | api-sports.io | ✅ |
| Forma últimos 5 partidos (quality-weighted) | api-sports.io | ✅ |
| Liga de origen (calidad de competición) | api-sports.io | ✅ |
| Posición en clasificación de liga | api-sports.io `/standings` | 🔧 Falta |
| Corners for/against promedio | api-sports.io `/fixtures/statistics` | 🔧 Falta |
| Tarjetas amarillas promedio | api-sports.io `/fixtures/statistics` | 🔧 Falta |
| xG (expected goals) | StatsBomb open data | 🔧 Solo WC hist. |
| Posesión promedio % | api-sports.io | 🔧 Falta |
| Disparos a puerta promedio | api-sports.io | 🔧 Falta |

### Features estáticas (no requieren API)
| Feature | Fuente | Nota |
|---------|--------|------|
| Títulos de Copa del Mundo | JSON estático (Wikipedia) | Max: Brasil 5, Alemania 4 |
| Títulos continentales (EURO/Copa Amér.) | JSON estático | |
| Ranking FIFA (pre-torneo) | JSON estático (publicado por FIFA) | |
| ELO histórico | clubelo.com o eloratings.net | CSV gratuito |
| Años desde última WC clasificación | Calculable de fixtures DB | |

### Features del partido
| Feature | Fuente | |
|---------|--------|--|
| ¿Es partido de local/visitante? | WC 2026 USA/CAN/MEX host | |
| Diferencia de ranking FIFA | Feature derivada | |
| Diferencia de ELO | Feature derivada | |
| Fase del torneo (grupo/R16/QF/SF/Final) | matches table | ✅ |
| Días de descanso desde último partido | Calculable | |

---

## Datos de entrenamiento

### Opción A: StatsBomb WC histórico (datos propios)
- **WC 2022 Qatar**: 64 partidos con eventos completos
- **WC 2018 Rusia**: 64 partidos con eventos completos
- **Total**: 128 partidos × 2 equipos = 256 observaciones
- **Problema**: pequeño para ML sofisticado, OK para logística/Poisson

### Opción B: api-sports.io histórico
- `/fixtures?league=1&season=2022` → resultados WC 2022
- `/fixtures?league=1&season=2018` → resultados WC 2018
- Con stats de cada equipo en sus ligas 6 meses antes del WC
- **Costo**: ~50 API calls (una sola vez, cacheado)

### Opción C: Datos externos (gratuitos)
- **football-data.co.uk**: CSV con resultados históricos WC, no requiere API
- **Kaggle WC dataset**: datos de todos los WC desde 1930
- **ELO ratings**: eloratings.net CSV público

**Recomendación**: A + C. StatsBomb para features de partido, eloratings.net para ELO histórico, JSON estático para trofeos/ranking FIFA.

---

## Pipeline de entrenamiento

```
1. collect_training_data.py
   ├── Carga resultados WC 2018+2022 de StatsBomb
   ├── Para cada equipo: obtiene stats de su liga 6 meses antes del WC
   │   (api-sports.io season 2018/2022 — una sola vez, cacheado)
   ├── Carga ELO ratings desde CSV
   ├── Merge → DataFrame de 256 filas × N features
   └── Guarda: data/training/wc_matches_features.parquet

2. train_model.py
   ├── Feature engineering (normalización, encoding)
   ├── Train/val split (WC2018=train, WC2022=val o k-fold)
   ├── Entrena Dixon-Coles / Logistic Regression / XGBoost
   ├── Calibración de probabilidades (Platt scaling)
   ├── Métricas: Brier score, log-loss, accuracy
   └── Guarda: models/wc_predictor.pkl + models/feature_names.json

3. predict_wc2026.py  (reemplaza scoring_model.py)
   ├── Carga modelo entrenado
   ├── Para cada partido: construye feature vector con datos actuales
   ├── Predice P(home_win), P(draw), P(away_win)
   ├── Convierte a confianza 0-100 y stake tier
   └── Escribe a predictions table (misma estructura)

4. recalibrate_model.py  (correr después de cada jornada)
   ├── Lee resultados reales de partidos WC 2026 jugados
   ├── Recalcula pesos del modelo (fine-tuning Bayesiano)
   └── Re-entrena con datos WC 2026 acumulados
```

---

## Features estáticas que necesito confirmar contigo

Necesito tu visto bueno antes de incluir como features estáticas (son datos públicos, no inventados):

```json
{
  "Argentina": {"fifa_rank": 1, "wc_titles": 3, "continental_titles": 16, "elo": 2141},
  "France":    {"fifa_rank": 2, "wc_titles": 2, "continental_titles": 2,  "elo": 2003},
  "Belgium":   {"fifa_rank": 3, "wc_titles": 0, "continental_titles": 0,  "elo": 1882},
  "Brazil":    {"fifa_rank": 5, "wc_titles": 5, "continental_titles": 9,  "elo": 2058},
  ...
}
```

Fuentes verificables:
- Ranking FIFA: **fifa.com/fifa-world-ranking** (publicado mensualmente)
- Títulos WC: **Wikipedia — FIFA World Cup winners**
- ELO: **eloratings.net** (CSV gratuito, actualizado en tiempo real)
- Títulos continentales: **Wikipedia** por confederación

¿Confirmamos que usar estos datos estáticos (verificables, públicos) está OK para el modelo?

---

## Datos adicionales para las cartillas (sin ML, solo display)

Estos campos puedo agregar al dashboard inmediatamente con datos estáticos + api-sports.io:

| Campo | Fuente | Costo API |
|-------|--------|-----------|
| Posición actual en liga | `/standings` | 10 calls (1/liga) |
| Últimos 5 partidos detallados | `/fixtures?last=5` | 48 calls |
| % posesión promedio | `/fixtures/statistics` | 48 calls |
| Disparos a puerta/p | `/fixtures/statistics` | incluido arriba |
| Ranking FIFA | JSON estático | 0 calls |
| Estrellas WC (títulos) | JSON estático | 0 calls |

**Total adicional**: ~100 calls → ejecutar en 2 días (50/día) o cuando la quota se libere.

---

## Cronograma propuesto

| Fase | Tarea | Cuándo |
|------|-------|--------|
| **Hoy** | Plan aprobado, JSON estático de rankings/trofeos | Ahora |
| **Día 2** | `collect_training_data.py` — extraer StatsBomb WC 2018+2022 | 1 sesión |
| **Día 3** | Enriquecer con ELO + api-sports.io stats pre-WC | 1 sesión + API calls |
| **Día 4** | `train_model.py` — Dixon-Coles baseline, métricas | 1 sesión |
| **Día 5** | Integrar predictor ML en pipeline diario | 1 sesión |
| **Torneo** | Recalibrar después de cada jornada con resultados reales | Automático |

---

## Preguntas abiertas (para confirmar antes de construir)

1. ¿Usamos ranking FIFA estático (pre-WC) o ELO dinámico (actualizado durante torneo)?
2. ¿Dixon-Coles (Poisson) o XGBoost? DS puede preferir el segundo por flexibilidad.
3. ¿Queremos predecir también over/under goles totales (2.5, 3.5)?
4. ¿Calibración: queremos probabilidades bien calibradas (Brier) o máxima accuracy?
5. ¿El modelo ML reemplaza el modelo heurístico o corre en paralelo primero?

---

## Archivos que se crearán

```
tools/
  collect_training_data.py   — extrae features históricas
  train_model.py             — entrena y guarda modelo
  predict_wc2026.py          — reemplaza scoring_model.py
  recalibrate_model.py       — fine-tuning post-jornada

models/
  wc_predictor.pkl           — modelo entrenado
  feature_names.json         — orden de features

data/
  static/
    fifa_rankings.json       — rankings pre-WC (estático)
    team_trophies.json       — WC titles + continental titles
    elo_ratings.json         — snapshot ELO pre-torneo
  training/
    wc_matches_features.parquet  — dataset de entrenamiento
```
