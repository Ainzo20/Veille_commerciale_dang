# Veille_backend — API Veille Marché Dang
**Commune de Ngaoundéré 3ème** — Région Adamaoua, Cameroun

## Installation & lancement

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
# → http://localhost:8000/docs
```

## Stack

| Couche | Technologie |
|---|---|
| API | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 |
| Validation | Pydantic v2 |
| BDD dev | SQLite |
| BDD prod | PostgreSQL (changer DATABASE_URL dans .env) |
| Push | Firebase Cloud Messaging |

## 9 Tables

| # | Table | Rôle |
|---|---|---|
| 1 | `categories_activite` | Familles de commerce (Alimentation, Textile…) |
| 2 | `activites` | Types précis de commerce avec mots-clés indexés |
| 3 | `zones_marche` | Secteurs géographiques du marché |
| 4 | `commercants` | Sédentaires + ambulants + semi-sédentaires |
| 5 | `sessions_journalieres` | Recette déclarée par commerçant et par jour |
| 6 | `indicateurs_activite` | CA + tendances précalculés par activité/période |
| 7 | `alertes` | Événements détectés automatiquement |
| 8 | `suivis_activite` | Abonnements mobiles à des activités (token_fcm) |
| 9 | `recherches_sauvegardees` | Veilles actives sur mots-clés (mobile) |

## Endpoints clés

| Méthode | Route | Description |
|---|---|---|
| GET | `/indicateurs/dashboard` | Vue d'accueil app mobile |
| POST | `/activites/` | Créer activité → alerte + push auto |
| POST | `/commercants/` | Enregistrer un commerçant |
| POST | `/sessions/` | Saisir une session journalière |
| GET | `/recherche/?q=mots` | Recherche full-text |
| POST | `/recherche/suivis` | S'abonner à une activité (mobile) |
| POST | `/recherche/sauvegardees` | Sauvegarder une veille active (mobile) |
| PATCH | `/alertes/{id}/lue` | Marquer alerte lue (mobile) |
| POST | `/indicateurs/recalculer` | Recalcul + vérification alertes |

## Flux de données

```
Fiche papier terrain
       ↓
Agent saisit dans backoffice web
       ↓
POST /sessions/  →  alerte_service vérifie pic CA + inactivité
POST /activites/ →  alerte_service crée alerte + push FCM
       ↓
App mobile Flutter (lecture seule)
  GET /indicateurs/dashboard
  GET /recherche/?q=
  GET /alertes/
  PATCH /alertes/{id}/lue
  POST /recherche/suivis       ← s'abonner à une activité
  POST /recherche/sauvegardees ← sauvegarder une veille
```

## Recalcul nocturne (cron)

```bash
# Chaque jour à 23h30
30 23 * * * curl -X POST http://localhost:8000/indicateurs/recalculer
```

## Migration PostgreSQL

```bash
# .env
DATABASE_URL=postgresql+psycopg2://user:pass@localhost/veille_dang
alembic init alembic && alembic revision --autogenerate -m "init"
alembic upgrade head
```
