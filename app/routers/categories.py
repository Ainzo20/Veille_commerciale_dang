from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import CategorieActivite
from app.schemas.schemas import CategorieCreate, CategorieUpdate, CategorieOut

router = APIRouter(prefix="/categories", tags=["Catégories"])


@router.get("/", response_model=list[CategorieOut])
def lister(actif_seulement: bool = True, db: Session = Depends(get_db)):
    q = db.query(CategorieActivite)
    if actif_seulement:
        q = q.filter(CategorieActivite.actif == True)
    return q.order_by(CategorieActivite.nom).all()


@router.get("/{cid}", response_model=CategorieOut)
def get_un(cid: int, db: Session = Depends(get_db)):
    c = db.get(CategorieActivite, cid)
    if not c: raise HTTPException(404, "Catégorie non trouvée")
    return c


@router.post("/", response_model=CategorieOut, status_code=201)
def creer(payload: CategorieCreate, db: Session = Depends(get_db)):
    if db.query(CategorieActivite).filter(CategorieActivite.nom.ilike(payload.nom)).first():
        raise HTTPException(409, f"Catégorie '{payload.nom}' existe déjà.")
    c = CategorieActivite(**payload.model_dump())
    db.add(c); db.commit(); db.refresh(c)
    return c


@router.patch("/{cid}", response_model=CategorieOut)
def modifier(cid: int, payload: CategorieUpdate, db: Session = Depends(get_db)):
    c = db.get(CategorieActivite, cid)
    if not c: raise HTTPException(404, "Catégorie non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit(); db.refresh(c)
    return c
