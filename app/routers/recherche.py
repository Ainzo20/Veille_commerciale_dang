from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import (
    Activite, Commercant, ZoneMarche,
    SuiviActivite, RechercheSauvegardee,
)
from app.schemas.schemas import (
    ReponseRecherche, ResultatRecherche,
    SuiviCreate, SuiviOut,
    RechercheCreate, RechercheOut,
)

router = APIRouter(prefix="/recherche", tags=["Recherche & Veille mobile"])


# ── Recherche full-text ───────────────────────────────────────────

@router.get("/", response_model=ReponseRecherche)
def rechercher(q: str = Query(..., min_length=2,
               description="Mots-clés — cherche dans activités, commerçants, zones"),
               db: Session = Depends(get_db)):
    termes = [t.strip() for t in q.lower().split() if len(t.strip()) >= 2]
    if not termes:
        return ReponseRecherche(query=q, nb_resultats=0, resultats=[])

    resultats: list[ResultatRecherche] = []

    # Activités
    for a in db.query(Activite).filter(Activite.actif == True).all():
        txt = " ".join(filter(None, [(a.nom or "").lower(),
                                      (a.mots_cles or "").lower(),
                                      (a.categorie.nom if a.categorie else "").lower()]))
        m = sum(1 for t in termes if t in txt)
        if m:
            resultats.append(ResultatRecherche(
                type="activite", id=a.id, label=a.nom,
                sous_label=a.categorie.nom if a.categorie else None,
                score=round(m / len(termes), 2)))

    # Commerçants
    for c in db.query(Commercant).filter(Commercant.actif == True).all():
        txt = " ".join(filter(None, [(c.nom_commercial or "").lower(),
                                      (c.telephone or "").lower(),
                                      (c.notes or "").lower(),
                                      (c.activite.nom if c.activite else "").lower()]))
        m = sum(1 for t in termes if t in txt)
        if m:
            resultats.append(ResultatRecherche(
                type="commercant", id=c.id,
                label=c.nom_commercial or c.telephone,
                sous_label=c.activite.nom if c.activite else None,
                score=round(m / len(termes), 2)))

    # Zones
    for z in db.query(ZoneMarche).filter(ZoneMarche.actif == True).all():
        txt = " ".join(filter(None, [(z.nom or "").lower(), (z.description or "").lower()]))
        m = sum(1 for t in termes if t in txt)
        if m:
            resultats.append(ResultatRecherche(
                type="zone", id=z.id, label=z.nom, sous_label=None,
                score=round(m / len(termes), 2)))

    resultats.sort(key=lambda r: r.score, reverse=True)
    return ReponseRecherche(query=q, nb_resultats=len(resultats), resultats=resultats)


# ── Suivi d'activité (mobile) ─────────────────────────────────────

@router.get("/suivis/{token_fcm}", response_model=list[SuiviOut])
def mes_suivis(token_fcm: str, db: Session = Depends(get_db)):
    return db.query(SuiviActivite).filter(SuiviActivite.token_fcm == token_fcm).all()


@router.post("/suivis", response_model=SuiviOut, status_code=201)
def suivre(payload: SuiviCreate, db: Session = Depends(get_db)):
    if not db.get(Activite, payload.activite_id):
        raise HTTPException(404, "Activité non trouvée")
    if db.query(SuiviActivite).filter(
            SuiviActivite.token_fcm   == payload.token_fcm,
            SuiviActivite.activite_id == payload.activite_id).first():
        raise HTTPException(409, "Vous suivez déjà cette activité.")
    s = SuiviActivite(**payload.model_dump())
    db.add(s); db.commit(); db.refresh(s)
    return s


@router.delete("/suivis/{sid}", status_code=204)
def ne_plus_suivre(sid: int, db: Session = Depends(get_db)):
    s = db.get(SuiviActivite, sid)
    if not s: raise HTTPException(404, "Suivi non trouvé")
    db.delete(s); db.commit()


# ── Recherches sauvegardées (mobile) ─────────────────────────────

@router.get("/sauvegardees/{token_fcm}", response_model=list[RechercheOut])
def mes_recherches(token_fcm: str, db: Session = Depends(get_db)):
    return (db.query(RechercheSauvegardee)
            .filter(RechercheSauvegardee.token_fcm == token_fcm)
            .order_by(RechercheSauvegardee.created_at.desc()).all())


@router.post("/sauvegardees", response_model=RechercheOut, status_code=201)
def sauvegarder(payload: RechercheCreate, db: Session = Depends(get_db)):
    """
    Enregistre une veille active.
    → Push notification automatique dès qu'une activité correspondante est créée.
    """
    if db.query(RechercheSauvegardee).filter(
            RechercheSauvegardee.token_fcm == payload.token_fcm,
            RechercheSauvegardee.mots_cles == payload.mots_cles).first():
        raise HTTPException(409, "Cette recherche est déjà sauvegardée.")
    r = RechercheSauvegardee(**payload.model_dump())
    db.add(r); db.commit(); db.refresh(r)
    return r


@router.delete("/sauvegardees/{rid}", status_code=204)
def supprimer_recherche(rid: int, db: Session = Depends(get_db)):
    r = db.get(RechercheSauvegardee, rid)
    if not r: raise HTTPException(404, "Recherche non trouvée")
    db.delete(r); db.commit()
