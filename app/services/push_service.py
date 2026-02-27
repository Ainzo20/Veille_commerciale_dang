"""
push_service.py — Firebase Cloud Messaging

Sans FCM_SERVER_KEY (mode dev) : log console, pas d'erreur.
Avec clé : appels HTTP FCM multicast (max 1000 tokens/requête).
"""
import logging
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
import httpx

from app.core.config import settings
from app.models.models import SuiviActivite, RechercheSauvegardee, Alerte, Activite

logger = logging.getLogger(__name__)
FCM_URL = "https://fcm.googleapis.com/fcm/send"


async def _send(tokens: list[str], title: str, body: str, data: dict = None) -> int:
    """Envoie aux tokens donnés. Retourne le nombre d'envois réussis."""
    if not tokens:
        return 0
    if not settings.FCM_SERVER_KEY:
        for t in tokens:
            logger.info(f"[PUSH-DEV] {t[:20]}… | {title} | {body}")
        return len(tokens)
    ok = 0
    headers = {"Authorization": f"key={settings.FCM_SERVER_KEY}",
               "Content-Type": "application/json"}
    for i in range(0, len(tokens), 1000):
        batch = tokens[i:i+1000]
        payload = {"registration_ids": batch,
                   "notification": {"title": title, "body": body, "sound": "default"},
                   "data": data or {}, "priority": "high"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(FCM_URL, json=payload, headers=headers)
                if r.status_code == 200:
                    ok += r.json().get("success", 0)
        except Exception as e:
            logger.error(f"FCM error: {e}")
    return ok


async def notify_nouvelle_activite(db: Session, activite: Activite) -> None:
    """
    Notifie les appareils dont une recherche sauvegardée correspond
    aux mots-clés ou au nom de la nouvelle activité.
    """
    termes = set()
    if activite.nom:
        termes.update(activite.nom.lower().split())
    if activite.mots_cles:
        termes.update(activite.mots_cles.lower().split())

    tokens = []
    for r in db.query(RechercheSauvegardee).all():
        if set(r.mots_cles.lower().split()) & termes:
            tokens.append(r.token_fcm)
            r.derniere_alerte_at = func.now()

    if tokens:
        db.commit()
        await _send(tokens,
                    "🆕 Nouvelle activité détectée",
                    f"« {activite.nom} » vient d'être enregistrée sur le marché de Dang.",
                    {"type": "nouvelle_activite", "activite_id": str(activite.id)})


async def notify_alerte(db: Session, alerte: Alerte, activite_id: int) -> None:
    """Notifie les abonnés d'une activité lors d'une alerte."""
    tokens = [s.token_fcm for s in
              db.query(SuiviActivite).filter(SuiviActivite.activite_id == activite_id).all()]
    labels = {
        "declin_activite": "📉 Activité en déclin",
        "pic_ca": "📈 Pic de chiffre d'affaires",
        "nouvelle_activite": "🆕 Nouvelle activité",
        "commercant_inactif": "⚠️ Commerçant inactif",
        "ambulant_disparu": "⚠️ Ambulant disparu",
        "mot_cle_match": "🔍 Veille active",
    }
    if tokens:
        n = await _send(tokens, labels.get(alerte.type_alerte, "📊 Alerte"), alerte.message,
                        {"type": alerte.type_alerte, "alerte_id": str(alerte.id)})
        alerte.push_envoyee = n > 0
        db.commit()


async def notify_rapport(tokens: list[str], mois_label: str) -> None:
    await _send(list(set(tokens)), "📊 Rapport mensuel disponible",
                f"Le rapport de {mois_label} est prêt. Consultez le tableau de bord.",
                {"type": "rapport_mensuel"})
