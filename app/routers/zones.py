from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import ZoneMarche
from app.schemas.schemas import ZoneCreate, ZoneUpdate, ZoneOut

router = APIRouter(prefix="/zones", tags=["Zones"])


@router.get("/", response_model=list[ZoneOut])
def lister(actif_seulement: bool = True, db: Session = Depends(get_db)):
    q = db.query(ZoneMarche)
    if actif_seulement: q = q.filter(ZoneMarche.actif == True)
    return q.order_by(ZoneMarche.nom).all()


@router.get("/{zid}", response_model=ZoneOut)
def get_un(zid: int, db: Session = Depends(get_db)):
    z = db.get(ZoneMarche, zid)
    if not z: raise HTTPException(404, "Zone non trouvée")
    return z


@router.post("/", response_model=ZoneOut, status_code=201)
def creer(payload: ZoneCreate, db: Session = Depends(get_db)):
    if db.query(ZoneMarche).filter(ZoneMarche.nom.ilike(payload.nom)).first():
        raise HTTPException(409, f"Zone '{payload.nom}' existe déjà.")
    z = ZoneMarche(**payload.model_dump())
    db.add(z); db.commit(); db.refresh(z)
    return z


@router.patch("/{zid}", response_model=ZoneOut)
def modifier(zid: int, payload: ZoneUpdate, db: Session = Depends(get_db)):
    z = db.get(ZoneMarche, zid)
    if not z: raise HTTPException(404, "Zone non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(z, k, v)
    db.commit(); db.refresh(z)
    return z
