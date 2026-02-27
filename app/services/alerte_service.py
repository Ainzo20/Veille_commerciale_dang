"""
alerte_service.py — Détection automatique des événements métier

Appelé :
  - après POST /activites/           → traiter_nouvelle_activite()
  - après POST /sessions/            → verifier_pic_ca(), verifier_inactivite()
  - depuis POST /indicateurs/recalculer → run_verifications_globales()
"""
import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import (
    Alerte, Activite, Commercant, SessionJournaliere,
    TypeAlerteEnum, NiveauAlerteEnum, StatutSessionEnum, TypePresenceEnum,
)
from app.services import calcul_service, push_service

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────

def _existe(db: Session, type_alerte: TypeAlerteEnum,
            activite_id: Optional[int] = None,
            commercant_id: Optional[int] = None,
            depuis: Optional[date] = None) -> bool:
    q = db.query(Alerte).filter(Alerte.type_alerte == type_alerte)
    if activite_id:   q = q.filter(Alerte.activite_id   == activite_id)
    if commercant_id: q = q.filter(Alerte.commercant_id == commercant_id)
    if depuis:        q = q.filter(Alerte.created_at    >= depuis.isoformat())
    return q.first() is not None


def _creer(db: Session, type_alerte: TypeAlerteEnum, niveau: NiveauAlerteEnum,
           message: str, activite_id: Optional[int] = None,
           commercant_id: Optional[int] = None) -> Alerte:
    a = Alerte(type_alerte=type_alerte, niveau=niveau, message=message,
               activite_id=activite_id, commercant_id=commercant_id)
    db.add(a); db.commit(); db.refresh(a)
    logger.info(f"Alerte [{type_alerte}] créée : {message[:80]}")
    return a


# ── Nouvelle activité ─────────────────────────────────────────────

async def traiter_nouvelle_activite(db: Session, activite: Activite) -> Alerte:
    """Appelée immédiatement après création dans le backoffice."""
    cat = activite.categorie.nom if activite.categorie else "—"
    msg = f"🆕 Nouvelle activité : « {activite.nom} » (catégorie : {cat}) enregistrée sur le marché de Dang."
    alerte = _creer(db, TypeAlerteEnum.NOUVELLE_ACTIVITE, NiveauAlerteEnum.INFO,
                    msg, activite_id=activite.id)
    await push_service.notify_nouvelle_activite(db, activite)
    await push_service.notify_alerte(db, alerte, activite.id)
    return alerte


# ── Déclin d'activité ─────────────────────────────────────────────

async def verifier_declin(db: Session, activite_id: int) -> Optional[Alerte]:
    """Déclin = CA en baisse SEUIL_DECLIN_PCT% sur 3 semaines consécutives."""
    today = date.today()
    cas = []
    for i in range(3):
        r = today - timedelta(weeks=i)
        cas.append(calcul_service.ca_periode(
            db, activite_id,
            calcul_service.debut_semaine(r),
            calcul_service.fin_semaine(r)))

    ca0, ca1, ca2 = cas  # ca0=courante, ca2=la plus ancienne
    if any(c is None for c in cas) or ca2 == 0:
        return None
    if not (ca0 < ca1 < ca2):
        return None
    baisse = (ca2 - ca0) / ca2 * 100
    if baisse < settings.SEUIL_DECLIN_PCT:
        return None

    debut_sem = calcul_service.debut_semaine(today)
    if _existe(db, TypeAlerteEnum.DECLIN_ACTIVITE, activite_id=activite_id, depuis=debut_sem):
        return None

    act = db.query(Activite).get(activite_id)
    nom = act.nom if act else f"#{activite_id}"
    niveau = NiveauAlerteEnum.CRITIQUE if baisse >= 50 else NiveauAlerteEnum.WARNING
    msg = (f"📉 « {nom} » en déclin : CA en baisse de {baisse:.0f}% sur 3 semaines "
           f"({ca2:,.0f} → {ca1:,.0f} → {ca0:,.0f} FCFA).")
    alerte = _creer(db, TypeAlerteEnum.DECLIN_ACTIVITE, niveau, msg, activite_id=activite_id)
    await push_service.notify_alerte(db, alerte, activite_id)
    return alerte


# ── Pic de CA ─────────────────────────────────────────────────────

async def verifier_pic_ca(db: Session, activite_id: int,
                           ca_jour: float) -> Optional[Alerte]:
    """Pic = CA journalier > SEUIL_PIC_MULTIPLICATEUR × moyenne."""
    today   = date.today()
    fenetre = today - timedelta(days=settings.FENETRE_ANALYSE)
    moy = (db.query(func.avg(SessionJournaliere.recette_journaliere))
           .filter(SessionJournaliere.activite_id == activite_id,
                   SessionJournaliere.statut == StatutSessionEnum.OUVERT,
                   SessionJournaliere.recette_journaliere.isnot(None),
                   SessionJournaliere.date_session >= fenetre,
                   SessionJournaliere.date_session < today).scalar())
    if not moy:
        return None
    moy = float(moy)
    if ca_jour < moy * settings.SEUIL_PIC_MULTIPLICATEUR:
        return None
    if _existe(db, TypeAlerteEnum.PIC_CA, activite_id=activite_id, depuis=today):
        return None

    act = db.query(Activite).get(activite_id)
    nom = act.nom if act else f"#{activite_id}"
    msg = (f"📈 Pic CA « {nom} » : {ca_jour:,.0f} FCFA aujourd'hui "
           f"({ca_jour/moy:.1f}× la moyenne de {moy:,.0f} FCFA).")
    alerte = _creer(db, TypeAlerteEnum.PIC_CA, NiveauAlerteEnum.INFO, msg, activite_id=activite_id)
    await push_service.notify_alerte(db, alerte, activite_id)
    return alerte


# ── Inactivité commerçant ─────────────────────────────────────────

async def verifier_inactivite(db: Session, commercant_id: int) -> Optional[Alerte]:
    """Absent > SEUIL_INACTIF_JOURS (sédentaire) ou SEUIL_AMBULANT_DISPARU (ambulant)."""
    com = db.query(Commercant).get(commercant_id)
    if not com or not com.actif:
        return None

    seuil = (settings.SEUIL_AMBULANT_DISPARU
             if com.type_presence == TypePresenceEnum.AMBULANT
             else settings.SEUIL_INACTIF_JOURS)
    type_alerte = (TypeAlerteEnum.AMBULANT_DISPARU
                   if com.type_presence == TypePresenceEnum.AMBULANT
                   else TypeAlerteEnum.COMMERCANT_INACTIF)

    derniere = (db.query(func.max(SessionJournaliere.date_session))
                .filter(SessionJournaliere.commercant_id == commercant_id,
                        SessionJournaliere.statut == StatutSessionEnum.OUVERT).scalar())
    if derniere is None:
        return None

    jours = (date.today() - derniere).days
    if jours < seuil:
        return None

    debut_sem = calcul_service.debut_semaine(date.today())
    if _existe(db, type_alerte, commercant_id=commercant_id, depuis=debut_sem):
        return None

    label = "marchand ambulant" if com.type_presence == TypePresenceEnum.AMBULANT else "commerçant"
    nom   = com.nom_commercial or com.telephone
    msg   = (f"⚠️ {label.capitalize()} « {nom} » absent depuis {jours} jours "
             f"(dernier relevé : {derniere.strftime('%d/%m/%Y')}).")
    alerte = _creer(db, type_alerte, NiveauAlerteEnum.WARNING, msg,
                    activite_id=com.activite_id, commercant_id=commercant_id)
    if com.activite_id:
        await push_service.notify_alerte(db, alerte, com.activite_id)
    return alerte


# ── Vérification globale (cron) ───────────────────────────────────

async def run_verifications_globales(db: Session) -> dict:
    """Lance toutes les vérifications sur toutes les activités et commerçants actifs."""
    res = {"declin": 0, "inactivite": 0}
    for act in db.query(Activite).filter(Activite.actif == True).all():
        if await verifier_declin(db, act.id):
            res["declin"] += 1
    for com in db.query(Commercant).filter(Commercant.actif == True).all():
        if await verifier_inactivite(db, com.id):
            res["inactivite"] += 1
    logger.info(f"Vérifications globales : {res}")
    return res
