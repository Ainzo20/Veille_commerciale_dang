from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import Activite, CategorieActivite
from app.schemas.schemas import ActiviteCreate, ActiviteUpdate, ActiviteOut
from app.services import alerte_service

router = APIRouter(prefix="/activites", tags=["Activités"])


def _load(db: Session, aid: int) -> Activite:
    a = (db.query(Activite)
         .options(joinedload(Activite.categorie))
         .filter(Activite.id == aid).first())
    if not a: raise HTTPException(404, "Activité non trouvée")
    return a


@router.get("/", response_model=list[ActiviteOut])
def lister(categorie_id: int | None = None, actif_seulement: bool = True,
           db: Session = Depends(get_db)):
    q = db.query(Activite).options(joinedload(Activite.categorie))
    if actif_seulement: q = q.filter(Activite.actif == True)
    if categorie_id:    q = q.filter(Activite.categorie_id == categorie_id)
    return q.order_by(Activite.nom).all()


@router.get("/{aid}", response_model=ActiviteOut)
def get_un(aid: int, db: Session = Depends(get_db)):
    return _load(db, aid)


@router.post("/", response_model=ActiviteOut, status_code=201)
async def creer(payload: ActiviteCreate, db: Session = Depends(get_db)):
    if not db.get(CategorieActivite, payload.categorie_id):
        raise HTTPException(404, "Catégorie non trouvée")
    if db.query(Activite).filter(Activite.nom.ilike(payload.nom)).first():
        raise HTTPException(409, f"Activité '{payload.nom}' existe déjà.")

    a = Activite(**payload.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    a = _load(db, a.id)
    # Déclenche alerte + push notifications automatiquement
    await alerte_service.traiter_nouvelle_activite(db, a)
    return a


@router.patch("/{aid}", response_model=ActiviteOut)
def modifier(aid: int, payload: ActiviteUpdate, db: Session = Depends(get_db)):
    a = db.get(Activite, aid)
    if not a: raise HTTPException(404, "Activité non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    return _load(db, aid)


@router.patch("/{aid}/activer", response_model=ActiviteOut)
def activer(aid: int, actif: bool = True, db: Session = Depends(get_db)):
    a = db.get(Activite, aid)
    if not a: raise HTTPException(404, "Activité non trouvée")
    a.actif = actif; db.commit()
    return _load(db, aid)
