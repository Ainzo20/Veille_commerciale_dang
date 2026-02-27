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
from app.schemas.schemas import IndicateurOut, DashboardOut, RecalculerRequest
from app.services import calcul_service, alerte_service

router = APIRouter(prefix="/indicateurs", tags=["Indicateurs & Dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    """Vue d'accueil de l'application mobile."""
    from app.models.models import Commercant as C, Activite as A, ZoneMarche as Z
    nb_com  = db.query(C).filter(C.actif == True).count()
    nb_act  = db.query(A).filter(A.actif == True).count()
    nb_zone = db.query(Z).filter(Z.actif == True).count()

    ca_cur  = calcul_service.ca_global_semaine_courante(db)
    ca_prev = calcul_service.ca_global_semaine_precedente(db)
    tendance, _ = calcul_service.calculer_tendance(ca_cur, ca_prev)

    nb_alertes = db.query(Alerte).filter(Alerte.lue == False).count()

    today   = date.today()
    d_debut = calcul_service.debut_mois(today)
    top5 = (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
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
        nb_commercants_actifs=nb_com, nb_activites_suivies=nb_act, nb_zones=nb_zone,
        ca_semaine_courante=ca_cur, ca_semaine_precedente=ca_prev,
        tendance_globale=tendance, nb_alertes_non_lues=nb_alertes,
        top_activites=top5, alertes_recentes=alertes5)


@router.get("/activite/{aid}", response_model=list[IndicateurOut])
def historique_activite(aid: int, periode: PeriodeEnum = PeriodeEnum.SEMAINE,
                         limit: int = Query(12, ge=1, le=52),
                         db: Session = Depends(get_db)):
    """Historique N périodes pour le graphique d'évolution mobile."""
    return (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
            .filter(IndicateurActivite.activite_id == aid,
                    IndicateurActivite.periode     == periode)
            .order_by(desc(IndicateurActivite.date_debut)).limit(limit).all())


@router.get("/top-activites", response_model=list[IndicateurOut])
def top_activites(periode: PeriodeEnum = PeriodeEnum.MOIS,
                   limit: int = Query(10, ge=1, le=50),
                   db: Session = Depends(get_db)):
    today = date.today()
    d = (calcul_service.debut_mois(today) if periode == PeriodeEnum.MOIS
         else calcul_service.debut_semaine(today) if periode == PeriodeEnum.SEMAINE
         else today)
    return (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
            .filter(IndicateurActivite.periode    == periode,
                    IndicateurActivite.date_debut == d,
                    IndicateurActivite.ca_total.isnot(None))
            .order_by(desc(IndicateurActivite.ca_total)).limit(limit).all())


@router.post("/recalculer")
async def recalculer(payload: RecalculerRequest = None, db: Session = Depends(get_db)):
    """Recalcule les indicateurs + vérifie les alertes. Sans corps = recalcul global."""
    if payload and payload.activite_id:
        periodes = [payload.periode] if payload.periode else list(PeriodeEnum)
        count = 0
        for p in periodes:
            calcul_service.recalculer_indicateur(db, payload.activite_id, p)
            count += 1
    else:
        count = calcul_service.recalculer_tout(db, payload.periode if payload else None)
    res = await alerte_service.run_verifications_globales(db)
    return {"indicateurs_recalcules": count, "alertes_creees": res}
