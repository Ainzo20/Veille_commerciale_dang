"""
sessions.py — Router des sessions journalières

LOGIQUE MÉTIER CENTRALE
═══════════════════════
Une session = bilan d'UN commerçant pour UN jour.
  • UNIQUE(commercant_id, date_session) — un seul bilan par jour.
  • recette_journaliere = total FCFA déclaré pour la journée entière.

Le CA s'accumule jour après jour :
  Lundi   → session → 15 000 FCFA
  Mardi   → session → 12 000 FCFA   ← nouveau jour = nouvelle session OK
  Mercredi→ session → 18 000 FCFA   ← idem
  ──────────────────────────────────
  CA activité cette semaine = 45 000 FCFA (3 sessions × 3 jours)

Essayer de créer 2 sessions le même jour pour le même commerçant → HTTP 409.
Pour corriger une recette : PATCH /sessions/{id}.

activite_id dans SessionCreate est OPTIONNEL :
  - Absent (cas courant) → déduit de Commercant.activite_id.
  - Présent → utilisé tel quel (semi-sédentaires qui changent d'activité).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import (
    SessionJournaliere, Commercant, Activite, ZoneMarche, StatutSessionEnum
)
from app.schemas.schemas import SessionCreate, SessionUpdate, SessionOut
from app.services import alerte_service

router = APIRouter(prefix="/sessions", tags=["Sessions journalières"])


def _load(db: Session, sid: int) -> SessionJournaliere:
    s = (db.query(SessionJournaliere)
         .options(
             joinedload(SessionJournaliere.commercant)
                 .joinedload(Commercant.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.zone_observation))
         .filter(SessionJournaliere.id == sid).first())
    if not s:
        raise HTTPException(404, "Session non trouvée")
    return s


@router.get("/", response_model=list[SessionOut])
def lister(
    commercant_id: int | None = None,
    activite_id:   int | None = None,
    zone_id:       int | None = None,
    limit:         int = 50,
    db: Session = Depends(get_db),
):
    q = (db.query(SessionJournaliere)
         .options(
             joinedload(SessionJournaliere.commercant)
                 .joinedload(Commercant.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.zone_observation)))
    if commercant_id:
        q = q.filter(SessionJournaliere.commercant_id == commercant_id)
    if activite_id:
        q = q.filter(SessionJournaliere.activite_id == activite_id)
    if zone_id:
        q = q.filter(SessionJournaliere.zone_observation_id == zone_id)
    return q.order_by(SessionJournaliere.date_session.desc()).limit(limit).all()


@router.get("/{sid}", response_model=SessionOut)
def get_un(sid: int, db: Session = Depends(get_db)):
    return _load(db, sid)


@router.post("/", response_model=SessionOut, status_code=201)
async def creer(payload: SessionCreate, db: Session = Depends(get_db)):
    """
    Créer le bilan journalier d'un commerçant.

    - Envoyer une session par commerçant par jour.
    - Pour accumuler le CA d'une activité, créer une session par jour
      pour chaque commerçant qui exerce cette activité.
    - activite_id est optionnel : s'il est absent, l'activité principale
      du commerçant est utilisée automatiquement.
    """
    # ── Vérification commerçant ─────────────────────────────────
    commercant = db.get(Commercant, payload.commercant_id)
    if not commercant:
        raise HTTPException(404, "Commerçant non trouvé")

    # ── Résolution activite_id ───────────────────────────────────
    # Si non fourni → on utilise l'activité principale du commerçant.
    # Cela évite à l'agent de ressaisir l'activité chaque jour.
    activite_id = payload.activite_id or commercant.activite_id

    if not db.get(Activite, activite_id):
        raise HTTPException(404, "Activité non trouvée")

    # ── Vérification zone ────────────────────────────────────────
    if payload.zone_observation_id and not db.get(ZoneMarche, payload.zone_observation_id):
        raise HTTPException(404, "Zone non trouvée")

    # ── Contrainte 1 session par commerçant par jour ─────────────
    existante = db.query(SessionJournaliere).filter(
        SessionJournaliere.commercant_id == payload.commercant_id,
        SessionJournaliere.date_session  == payload.date_session,
    ).first()

    if existante:
        raise HTTPException(
            409,
            f"Session déjà existante pour ce commerçant le {payload.date_session} "
            f"(id={existante.id}). "
            f"Pour corriger la recette, utilisez PATCH /sessions/{existante.id}. "
            f"Pour ajouter un nouveau jour, changez la date_session."
        )

    # ── Création ─────────────────────────────────────────────────
    data = payload.model_dump()
    data["activite_id"] = activite_id   # injecter l'activite_id résolu

    s = SessionJournaliere(**data)
    db.add(s)
    db.commit()
    db.refresh(s)

    # ── Vérifications alertes post-insertion ─────────────────────
    if payload.statut == StatutSessionEnum.OUVERT and payload.recette_journaliere:
        await alerte_service.verifier_pic_ca(db, activite_id, payload.recette_journaliere)
    await alerte_service.verifier_inactivite(db, payload.commercant_id)

    return _load(db, s.id)


@router.patch("/{sid}", response_model=SessionOut)
async def modifier(sid: int, payload: SessionUpdate, db: Session = Depends(get_db)):
    """
    Corriger le bilan d'une session existante.
    Cas typique : l'agent s'est trompé de recette et veut la corriger.
    """
    s = db.get(SessionJournaliere, sid)
    if not s:
        raise HTTPException(404, "Session non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    return _load(db, sid)


@router.delete("/{sid}", status_code=204)
def supprimer(sid: int, db: Session = Depends(get_db)):
    """Supprimer une session (correction d'erreur de saisie uniquement)."""
    s = db.get(SessionJournaliere, sid)
    if not s:
        raise HTTPException(404, "Session non trouvée")
    db.delete(s)
    db.commit()