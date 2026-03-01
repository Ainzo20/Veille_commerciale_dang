"""
alerte_service.py — Détection automatique des événements métier

═══════════════════════════════════════════════════════════════
QUI APPELLE CE SERVICE ?
═══════════════════════════════════════════════════════════════

  POST /activites/
    └─ traiter_nouvelle_activite()
         ├─ crée une Alerte NOUVELLE_ACTIVITE
         ├─ push FCM → abonnés des recherches sauvegardées (mots-clés)
         └─ push FCM → abonnés des suivis (activite_id)

  POST /sessions/        (si statut=ferme + recette)
  PATCH /sessions/{id}   (à la fermeture)
    ├─ verifier_pic_ca()
    │    ├─ compare CA du jour vs moyenne 30j
    │    ├─ si CA > SEUIL × moyenne → crée Alerte PIC_CA
    │    └─ push FCM → suivis de l'activité
    └─ verifier_inactivite()
         ├─ calcule jours depuis dernière session FERMÉE
         ├─ si absent > seuil → crée Alerte COMMERCANT_INACTIF / AMBULANT_DISPARU
         └─ push FCM → suivis de l'activité du commerçant

  POST /indicateurs/recalculer   (cron nocturne ou manuel)
    └─ run_verifications_globales()
         ├─ verifier_declin()     sur toutes les activités actives
         └─ verifier_inactivite() sur tous les commerçants actifs

═══════════════════════════════════════════════════════════════
DEUX TYPES DE NOTIFICATIONS PUSH
═══════════════════════════════════════════════════════════════

  1. SUIVIS ACTIVITÉ (SuiviActivite)
     L'utilisateur s'abonne à une activité spécifique (par ID).
     → notifié de toutes les alertes sur cette activité
     → pic CA, déclin, commerçant inactif, nouvelle activité
     Endpoint : POST /recherche/suivis  { token_fcm, activite_id }

  2. RECHERCHES SAUVEGARDÉES (RechercheSauvegardee)
     L'utilisateur dépose des mots-clés de veille.
     → notifié UNIQUEMENT quand une NOUVELLE activité est créée
       dont le nom ou les mots-clés contiennent ses termes
     Endpoint : POST /recherche/sauvegardees  { token_fcm, mots_cles }

  Exemple :
    Suivi activite_id=3     → alerte à chaque événement sur l'activité #3
    Recherche "riz céréales" → alerte si une nouvelle activité "Vente de riz"
                               est créée, quelle que soit son ID

═══════════════════════════════════════════════════════════════
RÈGLE CA DANS CE SERVICE
═══════════════════════════════════════════════════════════════
  Toujours filtrer statut = FERME pour les calculs CA et inactivité.
  Une session ouvert (en cours) n'a pas encore de recette déclarée
  et ne doit pas fausser les moyennes ni les détections d'absence.
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


# ══════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════

def _existe(db: Session, type_alerte: TypeAlerteEnum,
            activite_id: Optional[int] = None,
            commercant_id: Optional[int] = None,
            depuis: Optional[date] = None) -> bool:
    """
    Vérifie si une alerte du même type existe déjà récemment.
    Évite le spam d'alertes identiques sur la même période.
    """
    q = db.query(Alerte).filter(Alerte.type_alerte == type_alerte)
    if activite_id:   q = q.filter(Alerte.activite_id   == activite_id)
    if commercant_id: q = q.filter(Alerte.commercant_id == commercant_id)
    if depuis:        q = q.filter(Alerte.created_at    >= depuis.isoformat())
    return q.first() is not None


def _creer(db: Session, type_alerte: TypeAlerteEnum, niveau: NiveauAlerteEnum,
           message: str, activite_id: Optional[int] = None,
           commercant_id: Optional[int] = None) -> Alerte:
    """
    Crée et persiste une Alerte en base.
    Le push FCM est déclenché APRÈS par l'appelant (séparation des responsabilités).
    """
    a = Alerte(
        type_alerte   = type_alerte,
        niveau        = niveau,
        message       = message,
        activite_id   = activite_id,
        commercant_id = commercant_id,
    )
    db.add(a); db.commit(); db.refresh(a)
    logger.info(f"Alerte [{niveau}][{type_alerte}] créée : {message[:80]}")
    return a


# ══════════════════════════════════════════════════════════════
# 1. NOUVELLE ACTIVITÉ
# ══════════════════════════════════════════════════════════════

async def traiter_nouvelle_activite(db: Session, activite: Activite) -> Alerte:
    """
    Déclenché immédiatement après POST /activites/.

    Crée une alerte INFO et envoie DEUX types de notifications push :

    Push A — Recherches sauvegardées (push_service.notify_nouvelle_activite)
      → Parcourt tous les RechercheSauvegardee en base
      → Si mots_cles de la recherche ∩ mots du nom/mots_cles de l'activité ≠ ∅
        → push vers ce token_fcm
      Ex : recherche "riz céréales" + activité "Vente de riz" → match → push

    Push B — Suivis activité (push_service.notify_alerte)
      → Notifie les appareils qui suivent DÉJÀ cette activite_id
      → Peu probable au moment de la création (l'activité vient d'être créée)
        mais couvre le cas où l'ID est connu à l'avance
    """
    cat = activite.categorie.nom if activite.categorie else "—"
    msg = (f"🆕 Nouvelle activité : « {activite.nom} » "
           f"(catégorie : {cat}) enregistrée sur le marché de Dang.")

    alerte = _creer(db, TypeAlerteEnum.NOUVELLE_ACTIVITE, NiveauAlerteEnum.INFO,
                    msg, activite_id=activite.id)

    # Push A : appareils dont la veille par mots-clés correspond
    await push_service.notify_nouvelle_activite(db, activite)

    # Push B : appareils abonnés par suivi direct (activite_id)
    await push_service.notify_alerte(db, alerte, activite.id)

    return alerte


# ══════════════════════════════════════════════════════════════
# 2. PIC DE CA
# ══════════════════════════════════════════════════════════════

async def verifier_pic_ca(db: Session, activite_id: int,
                           ca_jour: float) -> Optional[Alerte]:
    """
    Déclenché après chaque fermeture de session (PATCH /sessions/{id}).

    Détection : CA du jour > SEUIL_PIC_MULTIPLICATEUR × moyenne des 30 derniers jours.

    Logique :
      1. Calcule la moyenne du CA journalier sur les SEUIL_FENETRE_ANALYSE derniers jours
         (sessions FERMÉES uniquement — recette connue et fiable)
      2. Compare ca_jour à cette moyenne
      3. Si dépasse le seuil ET aucune alerte PIC_CA aujourd'hui → crée alerte
      4. Push FCM vers tous les abonnés (SuiviActivite) de cette activité

    Anti-doublon : au plus 1 alerte PIC_CA par activité par jour.

    Exemple :
      moyenne 30j = 10 000 FCFA, seuil = 2.0×
      ca_jour = 25 000 FCFA → 25 000 > 20 000 → alerte créée
      ca_jour = 18 000 FCFA → 18 000 < 20 000 → rien
    """
    today   = date.today()
    fenetre = today - timedelta(days=settings.FENETRE_ANALYSE)

    # Moyenne sur sessions FERMÉES (recette déclarée et vérifiée)
    moy = (db.query(func.avg(SessionJournaliere.recette_journaliere))
           .filter(SessionJournaliere.activite_id == activite_id,
                   SessionJournaliere.statut == StatutSessionEnum.FERME,
                   SessionJournaliere.recette_journaliere.isnot(None),
                   SessionJournaliere.date_session >= fenetre,
                   SessionJournaliere.date_session <  today)
           .scalar())

    if not moy:
        return None   # pas assez d'historique pour calculer une moyenne

    moy = float(moy)
    if ca_jour < moy * settings.SEUIL_PIC_MULTIPLICATEUR:
        return None   # pic non atteint

    # Anti-doublon : une seule alerte PIC_CA par activité par jour
    if _existe(db, TypeAlerteEnum.PIC_CA, activite_id=activite_id, depuis=today):
        return None

    act = db.get(Activite, activite_id)
    nom = act.nom if act else f"#{activite_id}"
    msg = (f"📈 Pic CA « {nom} » : {ca_jour:,.0f} FCFA aujourd'hui "
           f"({ca_jour / moy:.1f}× la moyenne de {moy:,.0f} FCFA sur 30j).")

    alerte = _creer(db, TypeAlerteEnum.PIC_CA, NiveauAlerteEnum.INFO,
                    msg, activite_id=activite_id)

    # Push vers abonnés (SuiviActivite.activite_id = activite_id)
    await push_service.notify_alerte(db, alerte, activite_id)
    return alerte


# ══════════════════════════════════════════════════════════════
# 3. DÉCLIN D'ACTIVITÉ
# ══════════════════════════════════════════════════════════════

async def verifier_declin(db: Session, activite_id: int) -> Optional[Alerte]:
    """
    Déclenché par run_verifications_globales() (cron nocturne).

    Détection : CA en baisse ≥ SEUIL_DECLIN_PCT% sur 3 semaines consécutives.

    Logique :
      1. Calcule le CA des 3 dernières semaines ISO complètes
      2. Si ca_sem0 < ca_sem1 < ca_sem2 (baisse monotone)
         ET baisse totale ≥ SEUIL_DECLIN_PCT% → crée alerte
      3. Push FCM vers tous les abonnés (SuiviActivite) de cette activité

    Anti-doublon : au plus 1 alerte DECLIN_ACTIVITE par activité par semaine.

    Exemple :
      sem-2: 30 000, sem-1: 22 000, sem-0: 16 000
      baisse = (30 000 - 16 000) / 30 000 = 46.7% → alerte WARNING
      si baisse ≥ 50% → alerte CRITIQUE
    """
    today = date.today()
    cas   = []
    for i in range(3):
        r = today - timedelta(weeks=i)
        cas.append(calcul_service.ca_periode(
            db, activite_id,
            calcul_service.debut_semaine(r),
            calcul_service.fin_semaine(r),
        ))

    ca0, ca1, ca2 = cas   # ca0 = semaine courante, ca2 = il y a 2 semaines
    if any(c is None for c in cas) or ca2 == 0:
        return None

    if not (ca0 < ca1 < ca2):
        return None   # pas de baisse monotone

    baisse = (ca2 - ca0) / ca2 * 100
    if baisse < settings.SEUIL_DECLIN_PCT:
        return None

    debut_sem = calcul_service.debut_semaine(today)
    if _existe(db, TypeAlerteEnum.DECLIN_ACTIVITE,
               activite_id=activite_id, depuis=debut_sem):
        return None

    act    = db.get(Activite, activite_id)
    nom    = act.nom if act else f"#{activite_id}"
    niveau = (NiveauAlerteEnum.CRITIQUE if baisse >= 50
              else NiveauAlerteEnum.WARNING)
    msg = (f"📉 « {nom} » en déclin : CA en baisse de {baisse:.0f}% sur 3 semaines "
           f"({ca2:,.0f} → {ca1:,.0f} → {ca0:,.0f} FCFA).")

    alerte = _creer(db, TypeAlerteEnum.DECLIN_ACTIVITE, niveau,
                    msg, activite_id=activite_id)

    # Push vers abonnés (SuiviActivite.activite_id = activite_id)
    await push_service.notify_alerte(db, alerte, activite_id)
    return alerte


# ══════════════════════════════════════════════════════════════
# 4. INACTIVITÉ COMMERÇANT
# ══════════════════════════════════════════════════════════════

async def verifier_inactivite(db: Session, commercant_id: int) -> Optional[Alerte]:
    """
    Déclenché après POST /sessions/ ET par run_verifications_globales().

    Détection : aucune session FERMÉE depuis N jours
      → sédentaire/semi-sédentaire : SEUIL_INACTIF_JOURS jours  → COMMERCANT_INACTIF
      → ambulant                   : SEUIL_AMBULANT_DISPARU jours → AMBULANT_DISPARU

    Logique :
      1. Cherche la date de la dernière session FERMÉE du commerçant
         (sessions FERMÉES = recette déclarée = présence réelle confirmée)
      2. Si aucune session FERMÉE → on ne peut pas évaluer l'inactivité
      3. Si nb_jours ≥ seuil ET pas d'alerte cette semaine → crée alerte
      4. Push FCM vers abonnés de l'activité principale du commerçant

    Anti-doublon : au plus 1 alerte par commerçant par semaine.

    Pourquoi FERME et pas OUVERT ?
      Une session OUVERT signifie que l'agent est passé le matin
      mais n'a pas encore enregistré la recette. Ce n'est PAS
      une preuve d'activité économique réelle — seulement une
      présence physique non confirmée. Seule une session FERMÉE
      (recette déclarée) confirme que le commerçant a réellement
      exercé son activité.
    """
    com = db.get(Commercant, commercant_id)
    if not com or not com.actif:
        return None

    is_ambulant  = com.type_presence == TypePresenceEnum.AMBULANT
    seuil        = (settings.SEUIL_AMBULANT_DISPARU if is_ambulant
                    else settings.SEUIL_INACTIF_JOURS)
    type_alerte  = (TypeAlerteEnum.AMBULANT_DISPARU if is_ambulant
                    else TypeAlerteEnum.COMMERCANT_INACTIF)

    # Dernière activité réelle = dernière session FERMÉE avec recette
    derniere = (db.query(func.max(SessionJournaliere.date_session))
                .filter(SessionJournaliere.commercant_id == commercant_id,
                        SessionJournaliere.statut == StatutSessionEnum.FERME)
                .scalar())

    if derniere is None:
        return None   # jamais eu de session fermée → pas d'historique suffisant

    jours = (date.today() - derniere).days
    if jours < seuil:
        return None   # en dessous du seuil

    # Anti-doublon : une seule alerte par commerçant par semaine
    debut_sem = calcul_service.debut_semaine(date.today())
    if _existe(db, type_alerte, commercant_id=commercant_id, depuis=debut_sem):
        return None

    label = "marchand ambulant" if is_ambulant else "commerçant"
    nom   = com.nom_commercial or com.telephone
    msg   = (f"⚠️ {label.capitalize()} « {nom} » absent depuis {jours} jour{'s' if jours > 1 else ''} "
             f"(dernière session fermée : {derniere.strftime('%d/%m/%Y')}).")

    alerte = _creer(db, type_alerte, NiveauAlerteEnum.WARNING, msg,
                    activite_id=com.activite_id, commercant_id=commercant_id)

    # Push vers abonnés de l'activité principale du commerçant
    if com.activite_id:
        await push_service.notify_alerte(db, alerte, com.activite_id)

    return alerte


# ══════════════════════════════════════════════════════════════
# 5. VÉRIFICATION GLOBALE (CRON)
# ══════════════════════════════════════════════════════════════

async def run_verifications_globales(db: Session) -> dict:
    """
    Lance toutes les vérifications sur l'ensemble du marché.
    Appelé par POST /indicateurs/recalculer et par le cron nocturne.

    Retourne un dict avec le nombre d'alertes créées par type :
      {"declin": 2, "inactivite": 5}

    Le recalcul des indicateurs est fait AVANT (dans indicateurs.py),
    ce service s'occupe uniquement des alertes.
    """
    res = {"declin": 0, "inactivite": 0}

    # Déclin : vérifier toutes les activités actives
    for act in db.query(Activite).filter(Activite.actif == True).all():
        if await verifier_declin(db, act.id):
            res["declin"] += 1

    # Inactivité : vérifier tous les commerçants actifs
    for com in db.query(Commercant).filter(Commercant.actif == True).all():
        if await verifier_inactivite(db, com.id):
            res["inactivite"] += 1

    logger.info(f"Vérifications globales terminées : {res}")
    return res