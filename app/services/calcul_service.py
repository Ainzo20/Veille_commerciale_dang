"""
calcul_service.py — Calculs des indicateurs de performance

═══════════════════════════════════════════════════════════════
ARCHITECTURE DES CHIFFRES D'AFFAIRES
═══════════════════════════════════════════════════════════════

Toutes les valeurs de CA sont CALCULÉES à partir de SessionJournaliere
— jamais stockées en double dans Activite ou CategorieActivite.

  1. CA d'une activité sur une période
     → ca_periode(db, activite_id, d0, d1)
     = SUM(recette_journaliere) des sessions ouvertes de CETTE activité
       entre d0 et d1, tous commerçants confondus.
     Ex : 'Vente de maïs' semaine 28 = somme des sessions de Mama Bawa,
          Salihou et Moussa pour cette activité du lundi au dimanche.

  2. CA par catégorie sur une période
     → ca_categorie(db, categorie_id, d0, d1)
     = SUM de ca_periode pour toutes les activités de la catégorie.
     Ex : CA 'Alimentation' = maïs + haricot + huile + …

  3. CA global du marché sur une période
     → ca_global(db, d0, d1)
     = SUM(recette_journaliere) de TOUTES les sessions ouvertes.
     = SUM de tous les CA par catégorie.

  4. Indicateurs pré-calculés (table IndicateurActivite)
     → recalculer_indicateur() / recalculer_tout()
     UPSERT chaque nuit pour éviter les calculs lourds au moment de
     l'affichage du dashboard mobile.

═══════════════════════════════════════════════════════════════
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    SessionJournaliere, IndicateurActivite, Activite, CategorieActivite,
    StatutSessionEnum, PeriodeEnum, TendanceEnum,
)
from app.core.config import settings


# ── Helpers dates ────────────────────────────────────────────────

def debut_semaine(d: date) -> date:
    return d - timedelta(days=d.weekday())

def fin_semaine(d: date) -> date:
    return debut_semaine(d) + timedelta(days=6)

def debut_mois(d: date) -> date:
    return d.replace(day=1)

def fin_mois(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1) - timedelta(days=1)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


# ── CA d'une activité ────────────────────────────────────────────

def ca_periode(db: Session, activite_id: int,
               d_debut: date, d_fin: date) -> Optional[float]:
    """
    CA d'une activité sur une période.

    = SUM(recette_journaliere) de toutes les sessions ouvertes dont
      activite_id correspond, entre d_debut et d_fin inclus.

    Une activité peut avoir plusieurs sessions le même jour (un commerçant
    différent par session). Toutes ces sessions sont sommées.
    """
    r = (db.query(func.sum(SessionJournaliere.recette_journaliere))
         .filter(SessionJournaliere.activite_id == activite_id,
                 SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                 SessionJournaliere.recette_journaliere.isnot(None),
                 SessionJournaliere.date_session >= d_debut,
                 SessionJournaliere.date_session <= d_fin)
         .scalar())
    return float(r) if r is not None else None


def nb_commercants_actifs(db: Session, activite_id: int,
                           d_debut: date, d_fin: date) -> int:
    """Nombre de commerçants distincts actifs pour une activité sur une période."""
    r = (db.query(func.count(func.distinct(SessionJournaliere.commercant_id)))
         .filter(SessionJournaliere.activite_id == activite_id,
                 SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                 SessionJournaliere.date_session >= d_debut,
                 SessionJournaliere.date_session <= d_fin)
         .scalar())
    return int(r or 0)


def taux_presence_moyen(db: Session, activite_id: int,
                         d_debut: date, d_fin: date) -> Optional[float]:
    """Moyenne des taux de présence individuels (jours_ouverts / jours_observés)."""
    ids_rows = (db.query(func.distinct(SessionJournaliere.commercant_id))
                .filter(SessionJournaliere.activite_id == activite_id,
                        SessionJournaliere.date_session >= d_debut,
                        SessionJournaliere.date_session <= d_fin).all())
    ids = [r[0] for r in ids_rows]
    if not ids:
        return None
    taux_list = []
    for cid in ids:
        nb_obs = (db.query(func.count(SessionJournaliere.id))
                  .filter(SessionJournaliere.commercant_id == cid,
                          SessionJournaliere.date_session >= d_debut,
                          SessionJournaliere.date_session <= d_fin).scalar() or 0)
        nb_ouv = (db.query(func.count(SessionJournaliere.id))
                  .filter(SessionJournaliere.commercant_id == cid,
                          SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                          SessionJournaliere.date_session >= d_debut,
                          SessionJournaliere.date_session <= d_fin).scalar() or 0)
        if nb_obs > 0:
            taux_list.append(nb_ouv / nb_obs)
    return round(sum(taux_list) / len(taux_list), 4) if taux_list else None


def calculer_tendance(ca_act: Optional[float],
                       ca_prev: Optional[float]) -> tuple:
    """Retourne (TendanceEnum, ecart_pct)."""
    if ca_act is None or ca_prev is None or ca_prev == 0:
        return TendanceEnum.STABLE, None
    ecart = (ca_act - ca_prev) / ca_prev * 100
    if ecart >= 5:
        return TendanceEnum.HAUSSE, round(ecart, 2)
    if ecart <= -5:
        return TendanceEnum.BAISSE, round(ecart, 2)
    return TendanceEnum.STABLE, round(ecart, 2)


# ── CA par catégorie ─────────────────────────────────────────────

def ca_categorie(db: Session, categorie_id: int,
                 d_debut: date, d_fin: date) -> Optional[float]:
    """
    CA d'une catégorie d'activités sur une période.

    = SUM(recette_journaliere) des sessions ouvertes dont l'activité
      appartient à cette catégorie, entre d_debut et d_fin inclus.

    Calculé en une seule requête SQL avec jointure Activite → Session.
    """
    r = (db.query(func.sum(SessionJournaliere.recette_journaliere))
         .join(Activite, SessionJournaliere.activite_id == Activite.id)
         .filter(Activite.categorie_id == categorie_id,
                 Activite.actif == True,
                 SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                 SessionJournaliere.recette_journaliere.isnot(None),
                 SessionJournaliere.date_session >= d_debut,
                 SessionJournaliere.date_session <= d_fin)
         .scalar())
    return float(r) if r is not None else None


def ca_toutes_categories(db: Session,
                          d_debut: date, d_fin: date) -> list[dict]:
    """
    CA par catégorie pour une période.
    Retourne une liste triée par CA décroissant.

    Exemple de retour :
    [
      {"categorie_id": 1, "nom": "Alimentation", "icone": "🌾", "ca": 450000.0},
      {"categorie_id": 2, "nom": "Restauration",  "icone": "🍽️", "ca": 210000.0},
      ...
    ]
    """
    categories = (db.query(CategorieActivite)
                  .filter(CategorieActivite.actif == True)
                  .all())
    resultats = []
    for cat in categories:
        ca = ca_categorie(db, cat.id, d_debut, d_fin)
        resultats.append({
            "categorie_id": cat.id,
            "nom":          cat.nom,
            "icone":        cat.icone,
            "ca":           ca or 0.0,
        })
    return sorted(resultats, key=lambda x: x["ca"], reverse=True)


# ── CA global du marché ──────────────────────────────────────────

def ca_global(db: Session, d0: date, d1: date) -> Optional[float]:
    """
    CA global du marché sur une période.
    = SUM(recette_journaliere) de TOUTES les sessions ouvertes (toutes activités).
    = somme des CA de toutes les catégories.
    """
    r = (db.query(func.sum(SessionJournaliere.recette_journaliere))
         .filter(SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                 SessionJournaliere.recette_journaliere.isnot(None),
                 SessionJournaliere.date_session >= d0,
                 SessionJournaliere.date_session <= d1)
         .scalar())
    return float(r) if r is not None else None


def ca_global_semaine_courante(db: Session) -> Optional[float]:
    d = date.today()
    return ca_global(db, debut_semaine(d), fin_semaine(d))


def ca_global_semaine_precedente(db: Session) -> Optional[float]:
    d = debut_semaine(date.today()) - timedelta(weeks=1)
    return ca_global(db, d, d + timedelta(days=6))


# ── Recalcul des indicateurs (UPSERT) ────────────────────────────

def recalculer_indicateur(db: Session, activite_id: int,
                           periode: PeriodeEnum,
                           ref: Optional[date] = None) -> IndicateurActivite:
    """
    Calcule ou met à jour l'IndicateurActivite pour une activité et une période.
    Idempotent grâce à UNIQUE(activite_id, periode, date_debut).
    """
    ref = ref or date.today()

    if periode == PeriodeEnum.JOUR:
        d0, d1 = ref, ref
        p0, p1 = ref - timedelta(days=1), ref - timedelta(days=1)
    elif periode == PeriodeEnum.SEMAINE:
        d0, d1 = debut_semaine(ref), fin_semaine(ref)
        p0 = d0 - timedelta(weeks=1)
        p1 = p0 + timedelta(days=6)
    else:  # MOIS
        d0, d1 = debut_mois(ref), fin_mois(ref)
        p0 = debut_mois(date(ref.year if ref.month > 1 else ref.year - 1,
                              ref.month - 1 if ref.month > 1 else 12, 1))
        p1 = fin_mois(p0)

    ca_act  = ca_periode(db, activite_id, d0, d1)
    ca_prev = ca_periode(db, activite_id, p0, p1)
    tendance, ecart = calculer_tendance(ca_act, ca_prev)

    ind = (db.query(IndicateurActivite)
           .filter(IndicateurActivite.activite_id == activite_id,
                   IndicateurActivite.periode    == periode,
                   IndicateurActivite.date_debut == d0)
           .first())
    if ind is None:
        ind = IndicateurActivite(activite_id=activite_id, periode=periode,
                                  date_debut=d0, date_fin=d1)
        db.add(ind)

    ind.ca_total          = ca_act
    ind.nb_commercants    = nb_commercants_actifs(db, activite_id, d0, d1)
    ind.taux_presence_moy = taux_presence_moyen(db, activite_id, d0, d1)
    ind.tendance          = tendance
    ind.ecart_pct         = ecart
    db.commit()
    db.refresh(ind)
    return ind


def recalculer_tout(db: Session,
                     periode: Optional[PeriodeEnum] = None) -> int:
    """Recalcule tous les indicateurs (toutes activités actives, toutes périodes)."""
    activites = db.query(Activite).filter(Activite.actif == True).all()
    periodes  = [periode] if periode else list(PeriodeEnum)
    count = 0
    for a in activites:
        for p in periodes:
            recalculer_indicateur(db, a.id, p)
            count += 1
    return count
