"""
sessions.py — Router des sessions journalières

FLUX MÉTIER CORRECT
════════════════════════════════════════════════════════════

ÉTAPE 1 — Matin : OUVRIR (sans recette)
  POST /sessions/
  { "commercant_id": 1, "date_session": "2026-03-05" }
  → statut = ouvert, recette = null
  → NE contribue PAS au CA (session en cours)

ÉTAPE 2 — Soir : FERMER avec la recette
  PATCH /sessions/{id}
  { "statut": "ferme", "recette_journaliere": 15000 }
  → Recette enregistrée → CA de l'activité mis à jour IMMÉDIATEMENT
  → IndicateurActivite recalculé automatiquement (jour + semaine + mois)
  → Dashboard à jour dès l'appel suivant

SAISIE A POSTERIORI (tout en une fois) :
  POST /sessions/
  { "commercant_id": 1, "date_session": "2026-03-01",
    "statut": "ferme", "recette_journaliere": 15000 }
  → Crée ET ferme, recalcul immédiat

CORRECTION :
  PATCH /sessions/{id}  { "recette_journaliere": 17000 }
  → Recalcul automatique
"""

from datetime import date as date_type
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import (
    SessionJournaliere, Commercant, Activite, ZoneMarche,
    StatutSessionEnum, PeriodeEnum,
)
from app.schemas.schemas import SessionCreate, SessionUpdate, SessionOut
from app.services import alerte_service, calcul_service

router = APIRouter(prefix="/sessions", tags=["Sessions journalières"])


# ── Chargement complet d'une session ─────────────────────────────

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


# ── Recalcul automatique après fermeture ─────────────────────────

def _recalculer(db: Session, activite_id: int, ref: date_type):
    """
    Recalcule les indicateurs jour + semaine + mois pour cette activité.
    Déclenché automatiquement après chaque fermeture ou correction de recette.
    Silencieux en cas d'erreur (ne bloque pas la réponse HTTP).
    """
    for periode in list(PeriodeEnum):
        try:
            calcul_service.recalculer_indicateur(db, activite_id, periode, ref)
        except Exception:
            pass


# ── GET /sessions/scores — catalogue des scores de fiabilité ─────

@router.get("/scores")
def get_scores_fiabilite():
    """
    Retourne le catalogue des valeurs de score_fiabilite disponibles.
    Utilisé par l'UI pour afficher un select avec les options standards.

    Format de réponse :
    [
      {"valeur": 0.90, "cle": "fiche_agent", "label": "Fiche agent",
       "description": "Fiche papier vérifiée physiquement par l'agent"},
      ...
    ]
    """
    from app.schemas.schemas import SCORES_FIABILITE
    return SCORES_FIABILITE


# ── GET /sessions/ ────────────────────────────────────────────────

@router.get("/", response_model=list[SessionOut])
def lister(
    commercant_id: int | None = None,
    activite_id:   int | None = None,
    zone_id:       int | None = None,
    statut:        str | None = None,
    limit:         int = 50,
    db: Session = Depends(get_db),
):
    """Liste des sessions, filtrables par commerçant, activité, zone, statut."""
    q = (db.query(SessionJournaliere)
         .options(
             joinedload(SessionJournaliere.commercant)
                 .joinedload(Commercant.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.zone_observation)))
    if commercant_id: q = q.filter(SessionJournaliere.commercant_id == commercant_id)
    if activite_id:   q = q.filter(SessionJournaliere.activite_id   == activite_id)
    if zone_id:       q = q.filter(SessionJournaliere.zone_observation_id == zone_id)
    if statut:
        try:
            q = q.filter(SessionJournaliere.statut == StatutSessionEnum(statut))
        except ValueError:
            raise HTTPException(400, f"Statut invalide : {statut}. "
                                      f"Valeurs : ouvert, ferme, absent_maladie, "
                                      f"absent_approvisionnement, inconnu")
    return q.order_by(SessionJournaliere.date_session.desc()).limit(limit).all()


# ── GET /sessions/{sid} ───────────────────────────────────────────

@router.get("/{sid}", response_model=SessionOut)
def get_un(sid: int, db: Session = Depends(get_db)):
    return _load(db, sid)


# ── POST /sessions/ — OUVERTURE ───────────────────────────────────

@router.post("/", response_model=SessionOut, status_code=201)
async def ouvrir(payload: SessionCreate, db: Session = Depends(get_db)):
    """
    Ouvrir une session le matin (ou créer+fermer en une seule requête).

    Ouverture normale (matin) :
      { "commercant_id": 1, "date_session": "2026-03-05" }
      → statut=ouvert, recette=null, NE contribue PAS au CA

    Saisie a posteriori (recette connue d'emblée) :
      { "commercant_id": 1, "date_session": "2026-03-01",
        "statut": "ferme", "recette_journaliere": 15000 }
      → Crée + ferme immédiatement, CA mis à jour
    """
    # Vérifier commerçant
    commercant = db.get(Commercant, payload.commercant_id)
    if not commercant:
        raise HTTPException(404, "Commerçant non trouvé")

    # Résoudre activite_id (optionnel → hérité du commerçant)
    activite_id = payload.activite_id or commercant.activite_id
    if not db.get(Activite, activite_id):
        raise HTTPException(404, "Activité non trouvée")

    if payload.zone_observation_id and not db.get(ZoneMarche, payload.zone_observation_id):
        raise HTTPException(404, "Zone non trouvée")

    # Unicité : (commercant_id, activite_id, date_session)
    # Un commerçant peut avoir plusieurs sessions le même jour
    # à condition que ce soient des activités différentes.
    existante = db.query(SessionJournaliere).filter(
        SessionJournaliere.commercant_id == payload.commercant_id,
        SessionJournaliere.activite_id   == activite_id,
        SessionJournaliere.date_session  == payload.date_session,
    ).first()
    if existante:
        raise HTTPException(
            409,
            f"Session déjà existante pour ce commerçant + cette activité "
            f"le {payload.date_session} "
            f"(id={existante.id}, statut={existante.statut}). "
            f"→ Pour une autre activité ce même jour : créer une nouvelle session "
            f"avec un activite_id différent. "
            f"→ Pour fermer cette session : "
            f'PATCH /sessions/{existante.id} '
            f'{{"statut": "ferme", "recette_journaliere": <montant>}}'
        )

    # Créer
    data = payload.model_dump()
    data["activite_id"] = activite_id
    s = SessionJournaliere(**data)
    db.add(s); db.commit(); db.refresh(s)

    # Si créée directement fermée → recalcul immédiat
    if s.statut == StatutSessionEnum.FERME and s.recette_journaliere:
        ref = s.date_session if isinstance(s.date_session, date_type) else date_type.today()
        _recalculer(db, activite_id, ref)
        await alerte_service.verifier_pic_ca(db, activite_id, s.recette_journaliere)

    await alerte_service.verifier_inactivite(db, payload.commercant_id)
    return _load(db, s.id)


# ── PATCH /sessions/{sid} — FERMETURE / CORRECTION ───────────────

@router.patch("/{sid}", response_model=SessionOut)
async def fermer_ou_corriger(sid: int, payload: SessionUpdate,
                              db: Session = Depends(get_db)):
    """
    Fermer une session avec sa recette (soir) ou corriger une recette.

    Fermeture (cas principal) :
      { "statut": "ferme", "recette_journaliere": 15000 }
      → CA activité mis à jour, indicateurs recalculés automatiquement

    Correction de recette :
      { "recette_journaliere": 17000 }
      → CA recorrigé, indicateurs recalculés

    Signalement absence :
      { "statut": "absent_maladie" }
      → Pas d'impact CA
    """
    s = db.get(SessionJournaliere, sid)
    if not s:
        raise HTTPException(404, "Session non trouvée")

    patch_data = payload.model_dump(exclude_unset=True)

    # Vérification : fermeture sans recette = impossible
    closing = patch_data.get("statut") in ("ferme", StatutSessionEnum.FERME)
    if closing:
        recette_finale = patch_data.get("recette_journaliere") or s.recette_journaliere
        if not recette_finale:
            raise HTTPException(
                422,
                'Impossible de fermer sans recette. '
                'Envoyez {"statut": "ferme", "recette_journaliere": <montant>}'
            )

    # Appliquer
    for k, v in patch_data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)

    # Recalcul si fermeture OU correction de recette sur session déjà fermée
    doit_recalculer = (
        s.statut == StatutSessionEnum.FERME
        and s.recette_journaliere is not None
        and ("statut" in patch_data or "recette_journaliere" in patch_data)
    )
    if doit_recalculer:
        ref = s.date_session if isinstance(s.date_session, date_type) else date_type.today()
        _recalculer(db, s.activite_id, ref)
        await alerte_service.verifier_pic_ca(db, s.activite_id, s.recette_journaliere)

    return _load(db, sid)


# ── DELETE /sessions/{sid} ────────────────────────────────────────

@router.delete("/{sid}", status_code=204)
async def supprimer(sid: int, db: Session = Depends(get_db)):
    """
    Supprimer une session (correction d'erreur uniquement).
    Si la session contribuait au CA (fermée avec recette),
    les indicateurs sont automatiquement recalculés.
    """
    s = db.get(SessionJournaliere, sid)
    if not s:
        raise HTTPException(404, "Session non trouvée")

    activite_id  = s.activite_id
    contribuait  = s.statut == StatutSessionEnum.FERME and s.recette_journaliere
    ref = s.date_session if isinstance(s.date_session, date_type) else date_type.today()

    db.delete(s); db.commit()

    if contribuait:
        _recalculer(db, activite_id, ref)