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
    if not s: raise HTTPException(404, "Session non trouvée")
    return s


@router.get("/", response_model=list[SessionOut])
def lister(commercant_id: int | None = None, activite_id: int | None = None,
           zone_id: int | None = None, limit: int = 50,
           db: Session = Depends(get_db)):
    q = (db.query(SessionJournaliere)
         .options(
             joinedload(SessionJournaliere.commercant)
                 .joinedload(Commercant.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.activite).joinedload(Activite.categorie),
             joinedload(SessionJournaliere.zone_observation)))
    if commercant_id: q = q.filter(SessionJournaliere.commercant_id == commercant_id)
    if activite_id:   q = q.filter(SessionJournaliere.activite_id   == activite_id)
    if zone_id:       q = q.filter(SessionJournaliere.zone_observation_id == zone_id)
    return q.order_by(SessionJournaliere.date_session.desc()).limit(limit).all()


@router.get("/{sid}", response_model=SessionOut)
def get_un(sid: int, db: Session = Depends(get_db)):
    return _load(db, sid)


@router.post("/", response_model=SessionOut, status_code=201)
async def creer(payload: SessionCreate, db: Session = Depends(get_db)):
    if not db.get(Commercant, payload.commercant_id):
        raise HTTPException(404, "Commerçant non trouvé")
    if not db.get(Activite, payload.activite_id):
        raise HTTPException(404, "Activité non trouvée")
    if payload.zone_observation_id and not db.get(ZoneMarche, payload.zone_observation_id):
        raise HTTPException(404, "Zone non trouvée")

    # Contrainte unique (commercant_id, date_session)
    ex = db.query(SessionJournaliere).filter(
        SessionJournaliere.commercant_id == payload.commercant_id,
        SessionJournaliere.date_session  == payload.date_session).first()
    if ex:
        raise HTTPException(409,
            f"Session déjà existante pour ce commerçant le {payload.date_session} (id={ex.id}).")

    s = SessionJournaliere(**payload.model_dump())
    db.add(s); db.commit(); db.refresh(s)

    # Vérifications alertes post-insertion
    if payload.statut == StatutSessionEnum.OUVERT and payload.recette_journaliere:
        await alerte_service.verifier_pic_ca(db, payload.activite_id, payload.recette_journaliere)
    await alerte_service.verifier_inactivite(db, payload.commercant_id)

    return _load(db, s.id)


@router.patch("/{sid}", response_model=SessionOut)
async def modifier(sid: int, payload: SessionUpdate, db: Session = Depends(get_db)):
    s = db.get(SessionJournaliere, sid)
    if not s: raise HTTPException(404, "Session non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    return _load(db, sid)


@router.delete("/{sid}", status_code=204)
def supprimer(sid: int, db: Session = Depends(get_db)):
    """Correction d'erreur de saisie uniquement."""
    s = db.get(SessionJournaliere, sid)
    if not s: raise HTTPException(404, "Session non trouvée")
    db.delete(s); db.commit()
