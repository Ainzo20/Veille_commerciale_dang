from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import Commercant, Activite, ZoneMarche, SessionJournaliere
from app.schemas.schemas import CommercantCreate, CommercantUpdate, CommercantOut, SessionSummary

router = APIRouter(prefix="/commercants", tags=["Commerçants"])


def _load(db: Session, cid: int) -> Commercant:
    c = (db.query(Commercant)
         .options(joinedload(Commercant.activite).joinedload(Activite.categorie),
                  joinedload(Commercant.zone_principale))
         .filter(Commercant.id == cid).first())
    if not c: raise HTTPException(404, "Commerçant non trouvé")
    return c


@router.get("/", response_model=list[CommercantOut])
def lister(activite_id: int | None = None, zone_id: int | None = None,
           type_presence: str | None = None, actif_seulement: bool = True,
           db: Session = Depends(get_db)):
    q = (db.query(Commercant)
         .options(joinedload(Commercant.activite).joinedload(Activite.categorie),
                  joinedload(Commercant.zone_principale)))
    if actif_seulement: q = q.filter(Commercant.actif == True)
    if activite_id:     q = q.filter(Commercant.activite_id == activite_id)
    if zone_id:         q = q.filter(Commercant.zone_principale_id == zone_id)
    if type_presence:   q = q.filter(Commercant.type_presence == type_presence)
    return q.order_by(Commercant.nom_commercial, Commercant.telephone).all()


@router.get("/{cid}", response_model=CommercantOut)
def get_un(cid: int, db: Session = Depends(get_db)):
    return _load(db, cid)


@router.get("/{cid}/sessions", response_model=list[SessionSummary])
def historique(cid: int, limit: int = 30, db: Session = Depends(get_db)):
    if not db.get(Commercant, cid): raise HTTPException(404, "Commerçant non trouvé")
    return (db.query(SessionJournaliere)
            .filter(SessionJournaliere.commercant_id == cid)
            .order_by(SessionJournaliere.date_session.desc())
            .limit(limit).all())


@router.post("/", response_model=CommercantOut, status_code=201)
def creer(payload: CommercantCreate, db: Session = Depends(get_db)):
    if not db.get(Activite, payload.activite_id):
        raise HTTPException(404, "Activité non trouvée")
    if payload.zone_principale_id and not db.get(ZoneMarche, payload.zone_principale_id):
        raise HTTPException(404, "Zone non trouvée")
    ex = db.query(Commercant).filter(Commercant.telephone == payload.telephone).first()
    if ex: raise HTTPException(409, f"Téléphone '{payload.telephone}' déjà enregistré (id={ex.id}).")
    c = Commercant(**payload.model_dump())
    db.add(c); db.commit(); db.refresh(c)
    return _load(db, c.id)


@router.patch("/{cid}", response_model=CommercantOut)
def modifier(cid: int, payload: CommercantUpdate, db: Session = Depends(get_db)):
    c = db.get(Commercant, cid)
    if not c: raise HTTPException(404, "Commerçant non trouvé")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    return _load(db, cid)


@router.patch("/{cid}/activer", response_model=CommercantOut)
def activer(cid: int, actif: bool = True, db: Session = Depends(get_db)):
    c = db.get(Commercant, cid)
    if not c: raise HTTPException(404, "Commerçant non trouvé")
    c.actif = actif; db.commit()
    return _load(db, cid)
