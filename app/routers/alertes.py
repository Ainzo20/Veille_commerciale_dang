from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc
from app.database import get_db
from app.models.models import Alerte, Activite, Commercant
from app.schemas.schemas import AlerteOut, AlertePatchLue

router = APIRouter(prefix="/alertes", tags=["Alertes"])


def _load_q(db: Session):
    return (db.query(Alerte)
            .options(
                joinedload(Alerte.activite).joinedload(Activite.categorie),
                joinedload(Alerte.commercant)
                    .joinedload(Commercant.activite).joinedload(Activite.categorie)))


@router.get("/", response_model=list[AlerteOut])
def lister(lues: bool | None = None, activite_id: int | None = None,
           commercant_id: int | None = None,
           limit: int = Query(50, ge=1, le=200),
           db: Session = Depends(get_db)):
    q = _load_q(db)
    if lues is True:        q = q.filter(Alerte.lue == True)
    if lues is False:       q = q.filter(Alerte.lue == False)
    if activite_id:         q = q.filter(Alerte.activite_id   == activite_id)
    if commercant_id:       q = q.filter(Alerte.commercant_id == commercant_id)
    return q.order_by(desc(Alerte.created_at)).limit(limit).all()


@router.get("/{aid}", response_model=AlerteOut)
def get_un(aid: int, db: Session = Depends(get_db)):
    a = _load_q(db).filter(Alerte.id == aid).first()
    if not a: raise HTTPException(404, "Alerte non trouvée")
    return a


@router.patch("/{aid}/lue", response_model=AlerteOut)
def marquer_lue(aid: int, payload: AlertePatchLue = AlertePatchLue(),
                db: Session = Depends(get_db)):
    """Marquer une alerte comme lue depuis l'application mobile."""
    a = db.get(Alerte, aid)
    if not a: raise HTTPException(404, "Alerte non trouvée")
    a.lue = payload.lue; db.commit()
    return _load_q(db).filter(Alerte.id == aid).first()


@router.patch("/tout-lire", response_model=dict)
def tout_lire(db: Session = Depends(get_db)):
    nb = db.query(Alerte).filter(Alerte.lue == False).update({"lue": True})
    db.commit()
    return {"alertes_marquees": nb}
