"""
main.py — Point d'entrée API Veille Marché Dang

Lancement :
    uvicorn app.main:app --reload --port 8000

Docs interactives :
    http://localhost:8000/docs      (Swagger UI)
    http://localhost:8000/redoc     (ReDoc)

Architecture :
    Backoffice web  → POST (saisie agents après tournée terrain)
    App mobile Flutter → GET + push notifications (veille décideurs)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import engine, Base
from app.routers import (
    categories, activites, zones,
    commercants, sessions, indicateurs,
    alertes, recherche,
)

# Crée toutes les tables au démarrage (dev)
# En production → alembic upgrade head
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="API Veille Marché Dang",
    description=(
        "Système d'information de veille stratégique — activités commerciales "
        "du marché de Dang.\n\n"
        "**Commune de Ngaoundéré 3ème** — Région Adamaoua, Cameroun.\n\n"
        "**Backoffice web** : saisie des données par les agents.\n"
        "**Application mobile Flutter** : veille, alertes, recherche (lecture seule)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # restreindre en production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(categories.router)
app.include_router(activites.router)
app.include_router(zones.router)
app.include_router(commercants.router)
app.include_router(sessions.router)
app.include_router(indicateurs.router)
app.include_router(alertes.router)
app.include_router(recherche.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "projet": "Veille Marché Dang",
            "commune": "Ngaoundéré 3ème", "docs": "/docs"}

@app.get("/health", tags=["Health"])
def health():
    return {"status": "healthy"}
