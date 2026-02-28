"""
indicateurs.py — Routes des indicateurs et dashboard

RÈGLE CA FONDAMENTALE
══════════════════════════════════════════════════════════════
Seules les sessions statut='ferme' + recette_journaliere NOT NULL comptent.
  Ouverture matin → CA inchangé
  Fermeture soir  → CA += recette + recalcul IndicateurActivite automatique

DASHBOARD : données temps-réel
  ca_semaine, ca_par_categorie → calculés directement sur SessionJournaliere
  top_activites               → IndicateurActivite (recalculé à chaque fermeture)
"""

from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc
from app.database import get_db
from app.models.models import (
    IndicateurActivite, Activite, Commercant, ZoneMarche,
    Alerte, PeriodeEnum, SessionJournaliere, CategorieActivite,
    StatutSessionEnum,
)
from app.schemas.schemas import (
    IndicateurOut, DashboardOut, RecalculerRequest, CaCategorieOut,
)
from app.services import calcul_service, alerte_service

router = APIRouter(prefix="/indicateurs", tags=["Indicateurs & Dashboard"])


# ── Helpers directs (bypass IndicateurActivite, toujours frais) ──────────────

def _ca_direct(db: Session, d0: date, d1: date,
               activite_id: Optional[int] = None) -> Optional[float]:
    """SUM recettes des sessions FERMÉES sur [d0, d1], optionnellement filtrées par activité."""
    q = (db.query(func.sum(SessionJournaliere.recette_journaliere))
         .filter(
             SessionJournaliere.statut == StatutSessionEnum.FERME,
             SessionJournaliere.recette_journaliere.isnot(None),
             SessionJournaliere.date_session >= d0,
             SessionJournaliere.date_session <= d1,
         ))
    if activite_id:
        q = q.filter(SessionJournaliere.activite_id == activite_id)
    r = q.scalar()
    return float(r) if r else None


def _ca_categories_direct(db: Session, d0: date, d1: date) -> list[CaCategorieOut]:
    """CA par catégorie, calculé directement sur sessions FERMÉES."""
    cats = db.query(CategorieActivite).filter(CategorieActivite.actif == True).all()
    result = []
    for cat in cats:
        r = (db.query(func.sum(SessionJournaliere.recette_journaliere))
             .join(Activite, SessionJournaliere.activite_id == Activite.id)
             .filter(
                 Activite.categorie_id == cat.id,
                 Activite.actif == True,
                 SessionJournaliere.statut == StatutSessionEnum.FERME,
                 SessionJournaliere.recette_journaliere.isnot(None),
                 SessionJournaliere.date_session >= d0,
                 SessionJournaliere.date_session <= d1,
             ).scalar())
        result.append(CaCategorieOut(
            categorie_id=cat.id, nom=cat.nom, icone=cat.icone,
            ca=float(r) if r else 0.0,
        ))
    result.sort(key=lambda x: x.ca, reverse=True)
    return result


def _top_activites_direct(db: Session, d0: date, d1: date, limit: int = 5):
    """
    Top activités par CA calculé directement (sans IndicateurActivite).
    Utilisé en fallback si la table indicateurs est vide.
    """
    rows = (db.query(
                Activite.id,
                Activite.nom,
                func.sum(SessionJournaliere.recette_journaliere).label('ca'),
                func.count(func.distinct(SessionJournaliere.commercant_id)).label('nb_com'),
            )
            .join(SessionJournaliere, SessionJournaliere.activite_id == Activite.id)
            .filter(
                SessionJournaliere.statut == StatutSessionEnum.FERME,
                SessionJournaliere.recette_journaliere.isnot(None),
                SessionJournaliere.date_session >= d0,
                SessionJournaliere.date_session <= d1,
                Activite.actif == True,
            )
            .group_by(Activite.id, Activite.nom)
            .order_by(desc('ca'))
            .limit(limit)
            .all())
    return rows


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)):
    """
    Tableau de bord — toutes les valeurs de CA sont en temps-réel.

    Les CA (semaine, catégories) sont calculés directement sur
    SessionJournaliere à chaque appel — jamais périmés.

    Top activités : depuis IndicateurActivite mis à jour à chaque
    fermeture de session + fallback recalcul si table vide.
    """
    today  = date.today()
    d_sem0 = calcul_service.debut_semaine(today)
    d_sem1 = calcul_service.fin_semaine(today)
    d_pre0 = d_sem0 - timedelta(weeks=1)
    d_pre1 = d_pre0 + timedelta(days=6)
    d_mois = calcul_service.debut_mois(today)

    # KPIs
    nb_com     = db.query(Commercant).filter(Commercant.actif == True).count()
    nb_act     = db.query(Activite).filter(Activite.actif == True).count()
    nb_zone    = db.query(ZoneMarche).filter(ZoneMarche.actif == True).count()
    nb_alertes = db.query(Alerte).filter(Alerte.lue == False).count()

    # CA temps-réel
    ca_cur  = _ca_direct(db, d_sem0, d_sem1)
    ca_prev = _ca_direct(db, d_pre0, d_pre1)
    tendance, _ = calcul_service.calculer_tendance(ca_cur, ca_prev)

    # Répartition catégories temps-réel
    ca_par_cat = _ca_categories_direct(db, d_sem0, d_sem1)

    # Top 5 activités du mois depuis IndicateurActivite
    top5 = (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite)
                     .joinedload(Activite.categorie))
            .filter(IndicateurActivite.periode    == PeriodeEnum.MOIS,
                    IndicateurActivite.date_debut == d_mois,
                    IndicateurActivite.ca_total   >  0)
            .order_by(desc(IndicateurActivite.ca_total))
            .limit(5).all())

    # Fallback : si aucun indicateur → recalcul immédiat
    if not top5:
        calcul_service.recalculer_tout(db, PeriodeEnum.MOIS)
        top5 = (db.query(IndicateurActivite)
                .options(joinedload(IndicateurActivite.activite)
                         .joinedload(Activite.categorie))
                .filter(IndicateurActivite.periode    == PeriodeEnum.MOIS,
                        IndicateurActivite.date_debut == d_mois,
                        IndicateurActivite.ca_total   >  0)
                .order_by(desc(IndicateurActivite.ca_total))
                .limit(5).all())

    # Alertes non lues
    alertes5 = (db.query(Alerte)
                .options(
                    joinedload(Alerte.activite).joinedload(Activite.categorie),
                    joinedload(Alerte.commercant)
                        .joinedload(Commercant.activite).joinedload(Activite.categorie))
                .filter(Alerte.lue == False)
                .order_by(desc(Alerte.created_at)).limit(5).all())

    return DashboardOut(
        nb_commercants_actifs = nb_com,
        nb_activites_suivies  = nb_act,
        nb_zones              = nb_zone,
        ca_semaine_courante   = ca_cur,
        ca_semaine_precedente = ca_prev,
        tendance_globale      = tendance,
        nb_alertes_non_lues   = nb_alertes,
        ca_par_categorie      = ca_par_cat,
        top_activites         = top5,
        alertes_recentes      = alertes5,
    )


# ── HISTORIQUE ACTIVITÉ ───────────────────────────────────────────────────────

@router.get("/activite/{aid}", response_model=list[IndicateurOut])
def historique_activite(
    aid: int,
    periode: PeriodeEnum = PeriodeEnum.SEMAINE,
    limit: int = Query(12, ge=1, le=52),
    db: Session = Depends(get_db),
):
    return (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
            .filter(IndicateurActivite.activite_id == aid,
                    IndicateurActivite.periode     == periode)
            .order_by(desc(IndicateurActivite.date_debut)).limit(limit).all())


# ── TOP ACTIVITÉS ─────────────────────────────────────────────────────────────

@router.get("/top-activites", response_model=list[IndicateurOut])
def top_activites(
    periode: PeriodeEnum = PeriodeEnum.MOIS,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    today = date.today()
    d = (calcul_service.debut_mois(today)     if periode == PeriodeEnum.MOIS
         else calcul_service.debut_semaine(today) if periode == PeriodeEnum.SEMAINE
         else today)
    rows = (db.query(IndicateurActivite)
            .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
            .filter(IndicateurActivite.periode == periode,
                    IndicateurActivite.date_debut == d,
                    IndicateurActivite.ca_total > 0)
            .order_by(desc(IndicateurActivite.ca_total)).limit(limit).all())
    if not rows:
        calcul_service.recalculer_tout(db, periode)
        rows = (db.query(IndicateurActivite)
                .options(joinedload(IndicateurActivite.activite).joinedload(Activite.categorie))
                .filter(IndicateurActivite.periode == periode,
                        IndicateurActivite.date_debut == d,
                        IndicateurActivite.ca_total > 0)
                .order_by(desc(IndicateurActivite.ca_total)).limit(limit).all())
    return rows


# ── CA CATÉGORIES ─────────────────────────────────────────────────────────────

@router.get("/ca-categories")
def ca_categories(
    periode: str = "semaine",
    db: Session = Depends(get_db),
):
    today = date.today()
    if periode == "mois":
        d0, d1 = calcul_service.debut_mois(today), calcul_service.fin_mois(today)
    elif periode == "jour":
        d0, d1 = today, today
    else:
        d0, d1 = calcul_service.debut_semaine(today), calcul_service.fin_semaine(today)
    return _ca_categories_direct(db, d0, d1)


# ── RECALCUL MANUEL ───────────────────────────────────────────────────────────

@router.post("/recalculer")
async def recalculer(
    payload: RecalculerRequest = None,
    db: Session = Depends(get_db),
):
    """
    Force recalcul des indicateurs.
    Normalement déclenché auto à chaque fermeture de session.
    Utile pour corriger des données ou initialiser la base.
    """
    if payload and payload.activite_id:
        periodes = [payload.periode] if payload.periode else list(PeriodeEnum)
        count = sum(1 for p in periodes
                    for _ in [calcul_service.recalculer_indicateur(db, payload.activite_id, p)])
    else:
        count = calcul_service.recalculer_tout(db, payload.periode if payload else None)
    res = await alerte_service.run_verifications_globales(db)
    return {"indicateurs_recalcules": count, "alertes_creees": res}