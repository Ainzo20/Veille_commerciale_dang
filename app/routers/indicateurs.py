from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc
from app.database import get_db
from app.models.models import (
    IndicateurActivite, Activite, Commercant, ZoneMarche,
    Alerte, PeriodeEnum,
)
from app.schemas.schemas import (
    IndicateurOut, DashboardOut, RecalculerRequest, CaCategorieOut,
)
from app.services import calcul_service, alerte_service

router = APIRouter(prefix="/indicateurs", tags=["Indicateurs & Dashboard"])


# ── Dashboard principal ──────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    """
    Vue d'accueil de l'application mobile.

    Retourne en un seul appel :
      - Compteurs globaux (commerçants, activités, zones)
      - CA global semaine courante vs précédente + tendance
      - CA ventilé par catégorie (semaine courante)
      - Top 5 activités du mois courant
      - 5 dernières alertes non lues
    """
    nb_com  = db.query(Commercant).filter(Commercant.actif == True).count()
    nb_act  = db.query(Activite).filter(Activite.actif == True).count()
    nb_zone = db.query(ZoneMarche).filter(ZoneMarche.actif == True).count()

    ca_cur  = calcul_service.ca_global_semaine_courante(db)
    ca_prev = calcul_service.ca_global_semaine_precedente(db)
    tendance, _ = calcul_service.calculer_tendance(ca_cur, ca_prev)

    # CA par catégorie — semaine courante
    today = date.today()
    d0    = calcul_service.debut_semaine(today)
    d1    = calcul_service.fin_semaine(today)
    ca_cats = calcul_service.ca_toutes_categories(db, d0, d1)

    nb_alertes = db.query(Alerte).filter(Alerte.lue == False).count()

    d_debut = calcul_service.debut_mois(today)
    top5 = (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite)
                     .joinedload(Activite.categorie))
            .filter(IndicateurActivite.periode    == PeriodeEnum.MOIS,
                    IndicateurActivite.date_debut == d_debut,
                    IndicateurActivite.ca_total.isnot(None))
            .order_by(desc(IndicateurActivite.ca_total)).limit(5).all())

    alertes5 = (db.query(Alerte)
                .options(
                    joinedload(Alerte.activite).joinedload(Activite.categorie),
                    joinedload(Alerte.commercant)
                        .joinedload(Commercant.activite).joinedload(Activite.categorie))
                .filter(Alerte.lue == False)
                .order_by(desc(Alerte.created_at)).limit(5).all())

    return DashboardOut(
        nb_commercants_actifs  = nb_com,
        nb_activites_suivies   = nb_act,
        nb_zones               = nb_zone,
        ca_semaine_courante    = ca_cur,
        ca_semaine_precedente  = ca_prev,
        tendance_globale       = tendance,
        ca_par_categorie       = [CaCategorieOut(**c) for c in ca_cats],
        nb_alertes_non_lues    = nb_alertes,
        top_activites          = top5,
        alertes_recentes       = alertes5,
    )


# ── CA par catégorie (endpoint dédié) ────────────────────────────

@router.get("/ca-categories", response_model=list[CaCategorieOut])
def ca_categories(
    periode: PeriodeEnum = PeriodeEnum.SEMAINE,
    db: Session = Depends(get_db),
):
    """
    CA ventilé par catégorie d'activités pour la période courante.

    Utile pour le graphique camembert / barres de l'app mobile.
    periode=semaine → semaine ISO en cours
    periode=mois    → mois calendaire en cours
    """
    today = date.today()
    if periode == PeriodeEnum.MOIS:
        d0 = calcul_service.debut_mois(today)
        d1 = calcul_service.fin_mois(today)
    elif periode == PeriodeEnum.SEMAINE:
        d0 = calcul_service.debut_semaine(today)
        d1 = calcul_service.fin_semaine(today)
    else:  # JOUR
        d0 = d1 = today

    return [CaCategorieOut(**c) for c in calcul_service.ca_toutes_categories(db, d0, d1)]


# ── Historique d'une activité ────────────────────────────────────

@router.get("/activite/{aid}", response_model=list[IndicateurOut])
def historique_activite(
    aid: int,
    periode: PeriodeEnum = PeriodeEnum.SEMAINE,
    limit: int = Query(12, ge=1, le=52),
    db: Session = Depends(get_db),
):
    """Historique N périodes pour le graphique d'évolution mobile."""
    return (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite)
                     .joinedload(Activite.categorie))
            .filter(IndicateurActivite.activite_id == aid,
                    IndicateurActivite.periode     == periode)
            .order_by(desc(IndicateurActivite.date_debut)).limit(limit).all())


# ── Top activités ────────────────────────────────────────────────

@router.get("/top-activites", response_model=list[IndicateurOut])
def top_activites(
    periode: PeriodeEnum = PeriodeEnum.MOIS,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top N activités par CA sur la période courante."""
    today = date.today()
    if periode == PeriodeEnum.MOIS:
        d = calcul_service.debut_mois(today)
    elif periode == PeriodeEnum.SEMAINE:
        d = calcul_service.debut_semaine(today)
    else:
        d = today

    return (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite)
                     .joinedload(Activite.categorie))
            .filter(IndicateurActivite.periode    == periode,
                    IndicateurActivite.date_debut == d,
                    IndicateurActivite.ca_total.isnot(None))
            .order_by(desc(IndicateurActivite.ca_total)).limit(limit).all())


# ── Recalcul ─────────────────────────────────────────────────────

@router.post("/recalculer")
async def recalculer(
    payload: Optional[RecalculerRequest] = None,
    db: Session = Depends(get_db),
):
    """
    Recalcule les indicateurs + vérifie les alertes.
    Sans corps = recalcul global (toutes activités, toutes périodes).
    """
    if payload and payload.activite_id:
        periodes = [payload.periode] if payload.periode else list(PeriodeEnum)
        count = 0
        for p in periodes:
            calcul_service.recalculer_indicateur(db, payload.activite_id, p)
            count += 1
    else:
        count = calcul_service.recalculer_tout(
            db, payload.periode if payload else None
        )

    alertes_creees = await alerte_service.run_verifications_globales(db)
    return {"indicateurs_recalcules": count, "alertes_creees": alertes_creees}
