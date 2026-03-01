"""
push_service.py — Firebase Cloud Messaging (FCM)

═══════════════════════════════════════════════════════════════
QU'EST-CE QUE FCM ?
═══════════════════════════════════════════════════════════════
Firebase Cloud Messaging est le service Google qui achemine les
notifications push vers les appareils Android et iOS.

Fonctionnement :
  1. L'app Flutter démarre → Firebase SDK génère un token unique
     pour cet appareil (ex: "dGhpcyBpcyBhIHRva2Vu_abc123…")
  2. L'app envoie ce token au backend → stocké dans SuiviActivite
     ou RechercheSauvegardee selon l'intention
  3. Quand un événement se produit :
       backend → POST fcm.googleapis.com (avec le token + message)
               → Firebase → appareil de l'utilisateur
  4. L'utilisateur voit la notification même si l'app est fermée

En mode DEV (FCM_SERVER_KEY absent) : les notifications sont
loggées dans la console au lieu d'être envoyées.

═══════════════════════════════════════════════════════════════
DEUX FONCTIONS PRINCIPALES
═══════════════════════════════════════════════════════════════

  notify_nouvelle_activite(db, activite)
    → Cible : RechercheSauvegardee (veille par mots-clés)
    → Déclenché uniquement à la CRÉATION d'une nouvelle activité
    → Match si mots_cles ∩ mots du nom/mots_cles de l'activité ≠ ∅
    → Met à jour derniere_alerte_at sur les recherches matchées

  notify_alerte(db, alerte, activite_id)
    → Cible : SuiviActivite (abonnement direct par activite_id)
    → Déclenché pour TOUTE alerte sur l'activité suivie
      (pic CA, déclin, commerçant inactif, ambulant disparu…)
    → Met à jour alerte.push_envoyee = True si au moins 1 envoi réussi

═══════════════════════════════════════════════════════════════
GESTION DES TOKENS EXPIRÉS
═══════════════════════════════════════════════════════════════
Les tokens FCM peuvent expirer si l'utilisateur désinstalle l'app
ou si Firebase les renouvelle. En prod, FCM retourne des erreurs
spécifiques (InvalidRegistration, NotRegistered) qu'il faudrait
intercepter pour supprimer les tokens invalides de la base.
Actuellement les erreurs sont loggées mais les tokens ne sont
pas nettoyés automatiquement — à implémenter en prod.

═══════════════════════════════════════════════════════════════
ENVOI EN BATCH
═══════════════════════════════════════════════════════════════
FCM accepte jusqu'à 1000 tokens par requête (multicast).
_send() découpe automatiquement en lots de 1000.
"""

import logging
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
import httpx

from app.core.config import settings
from app.models.models import SuiviActivite, RechercheSauvegardee, Alerte, Activite

logger = logging.getLogger(__name__)
FCM_URL = "https://fcm.googleapis.com/fcm/send"


# ══════════════════════════════════════════════════════════════
# ENVOI HTTP FCM
# ══════════════════════════════════════════════════════════════

async def _send(tokens: list[str], title: str, body: str,
                data: dict = None) -> int:
    """
    Envoie une notification push à une liste de tokens FCM.

    En mode DEV (FCM_SERVER_KEY non configuré) :
      → log console uniquement, aucun appel HTTP, aucune erreur

    En mode PROD :
      → découpe en lots de 1000 (limite FCM multicast)
      → retourne le nombre total d'envois réussis (success count FCM)
      → les erreurs réseau sont loggées et ignorées (non bloquantes)

    Args:
        tokens : liste des token_fcm destinataires
        title  : titre de la notification (affiché en gras)
        body   : corps du message
        data   : payload silencieux optionnel (type, ids…) accessible
                 dans l'app même sans interaction utilisateur

    Returns:
        Nombre d'envois réussis (0 si dev ou erreur)
    """
    if not tokens:
        return 0

    # Mode DEV — log uniquement
    if not settings.FCM_SERVER_KEY:
        for t in tokens:
            logger.info(f"[PUSH-DEV] token={t[:20]}… | {title} | {body}")
        return len(tokens)

    # Mode PROD — envoi HTTP FCM
    ok = 0
    headers = {
        "Authorization": f"key={settings.FCM_SERVER_KEY}",
        "Content-Type":  "application/json",
    }
    for i in range(0, len(tokens), 1000):
        batch   = tokens[i:i + 1000]
        payload = {
            "registration_ids": batch,
            "notification": {
                "title": title,
                "body":  body,
                "sound": "default",
            },
            "data":     data or {},
            "priority": "high",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(FCM_URL, json=payload, headers=headers)
                if r.status_code == 200:
                    result = r.json()
                    ok += result.get("success", 0)
                    # TODO prod : parcourir result["results"] pour nettoyer
                    # les tokens avec error = "NotRegistered" ou "InvalidRegistration"
                    failed = result.get("failure", 0)
                    if failed:
                        logger.warning(f"FCM: {failed} échec(s) sur {len(batch)} tokens")
                else:
                    logger.error(f"FCM HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"FCM connexion error: {e}")
    return ok


# ══════════════════════════════════════════════════════════════
# PUSH A — NOUVELLE ACTIVITÉ → RECHERCHES SAUVEGARDÉES
# ══════════════════════════════════════════════════════════════

async def notify_nouvelle_activite(db: Session, activite: Activite) -> int:
    """
    Notifie les appareils dont une recherche sauvegardée correspond
    aux mots du nom ou des mots-clés de la nouvelle activité.

    Algorithme de matching (intersection de mots) :
      termes_activite = mots du nom + mots des mots_cles
      termes_recherche = mots du mots_cles de la RechercheSauvegardee
      match = termes_activite ∩ termes_recherche ≠ ∅

    Exemple :
      activite.nom      = "Vente de riz en gros"
      activite.mots_cles= "céréales alimentation"
      termes_activite   = {"vente", "de", "riz", "en", "gros", "céréales", "alimentation"}

      recherche.mots_cles = "riz céréales"
      termes_recherche    = {"riz", "céréales"}

      intersection = {"riz", "céréales"} → match → push envoyé

    Met à jour RechercheSauvegardee.derniere_alerte_at pour les matchés.

    Returns:
        Nombre d'appareils notifiés
    """
    termes: set[str] = set()
    if activite.nom:
        termes.update(activite.nom.lower().split())
    if activite.mots_cles:
        termes.update(activite.mots_cles.lower().split())

    tokens: list[str] = []
    for recherche in db.query(RechercheSauvegardee).all():
        termes_recherche = set(recherche.mots_cles.lower().split())
        if termes_recherche & termes:          # intersection non vide = match
            tokens.append(recherche.token_fcm)
            recherche.derniere_alerte_at = func.now()

    if not tokens:
        return 0

    db.commit()
    nb = await _send(
        tokens,
        "🆕 Nouvelle activité détectée",
        f"« {activite.nom} » vient d'être enregistrée sur le marché de Dang.",
        {"type": "nouvelle_activite", "activite_id": str(activite.id)},
    )
    logger.info(f"notify_nouvelle_activite: {nb} push envoyés pour activite_id={activite.id}")
    return nb


# ══════════════════════════════════════════════════════════════
# PUSH B — ALERTE → SUIVIS ACTIVITÉ
# ══════════════════════════════════════════════════════════════

async def notify_alerte(db: Session, alerte: Alerte, activite_id: int) -> int:
    """
    Notifie tous les appareils abonnés à une activité via SuiviActivite.

    Appelé après création de TOUTE alerte sur une activité :
      - PIC_CA         → "📈 Pic de chiffre d'affaires"
      - DECLIN_ACTIVITE→ "📉 Activité en déclin"
      - NOUVELLE_ACTIVITE→ "🆕 Nouvelle activité"
      - COMMERCANT_INACTIF→ "⚠️ Commerçant inactif"
      - AMBULANT_DISPARU  → "⚠️ Ambulant disparu"

    Le payload data contient type + alerte_id pour que l'app Flutter
    puisse naviguer directement vers l'écran alerte correspondant.

    Met à jour alerte.push_envoyee = True si au moins 1 push réussi.

    Returns:
        Nombre d'appareils notifiés
    """
    tokens = [
        s.token_fcm for s in
        db.query(SuiviActivite)
          .filter(SuiviActivite.activite_id == activite_id)
          .all()
    ]

    if not tokens:
        logger.debug(f"notify_alerte: aucun abonné pour activite_id={activite_id}")
        return 0

    titres = {
        "declin_activite":   "📉 Activité en déclin",
        "pic_ca":            "📈 Pic de chiffre d'affaires",
        "nouvelle_activite": "🆕 Nouvelle activité",
        "commercant_inactif":"⚠️ Commerçant inactif",
        "ambulant_disparu":  "⚠️ Ambulant disparu",
        "mot_cle_match":     "🔍 Veille active",
    }
    titre = titres.get(str(alerte.type_alerte), "📊 Alerte Marché Dang")

    nb = await _send(
        tokens,
        titre,
        alerte.message,
        {"type": str(alerte.type_alerte), "alerte_id": str(alerte.id)},
    )

    if nb > 0:
        alerte.push_envoyee = True
        db.commit()

    logger.info(f"notify_alerte [{alerte.type_alerte}]: {nb} push envoyés "
                f"pour activite_id={activite_id}")
    return nb


# ══════════════════════════════════════════════════════════════
# PUSH RAPPORT MENSUEL
# ══════════════════════════════════════════════════════════════

async def notify_rapport(tokens: list[str], mois_label: str) -> int:
    """
    Envoie une notification de rapport mensuel à une liste de tokens.
    Appelé manuellement ou par tâche planifiée en fin de mois.

    Args:
        tokens     : liste des token_fcm destinataires
        mois_label : ex. "février 2026"

    Returns:
        Nombre d'appareils notifiés
    """
    return await _send(
        list(set(tokens)),    # dédoublonner les tokens
        "📊 Rapport mensuel disponible",
        f"Le rapport de {mois_label} est prêt. Consultez le tableau de bord.",
        {"type": "rapport_mensuel"},
    )