"""
activites.py — Router des activités

Endpoints :
  GET  /activites/              → liste légère (ActiviteOut)
  GET  /activites/{id}          → fiche légère (ActiviteOut)
  GET  /activites/{id}/detail   → fiche complète avec stats (ActiviteDetailOut)
                                   ↳ nb sessions, CA, commerçants avec leurs stats
  POST /activites/              → créer
  PATCH /activites/{id}         → modifier
  PATCH /activites/{id}/activer → activer/désactiver
"""

from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app.database import get_db
from app.models.models import (
    Activite, CategorieActivite, Commercant,
    SessionJournaliere, ZoneMarche,
    StatutSessionEnum,
)
from app.schemas.schemas import (
    ActiviteCreate, ActiviteUpdate, ActiviteOut,
    ActiviteDetailOut, CommercantActiviteOut, SessionRecente,
)
from app.services import alerte_service, calcul_service

router = APIRouter(prefix="/activites", tags=["Activités"])


# ── Helpers ──────────────────────────────────────────────────────

def _load(db: Session, aid: int) -> Activite:
    """Charge une activité avec sa catégorie (version légère)."""
    a = (db.query(Activite)
         .options(joinedload(Activite.categorie))
         .filter(Activite.id == aid).first())
    if not a:
        raise HTTPException(404, "Activité non trouvée")
    return a


# ── GET /activites/ ───────────────────────────────────────────────

@router.get("/", response_model=list[ActiviteOut])
def lister(
    categorie_id:   int | None = None,
    actif_seulement: bool = True,
    db: Session = Depends(get_db),
):
    """
    Liste de toutes les activités avec leur CA total cumulé.
    Réponse légère — sans détail des sessions ni des commerçants.
    Pour les détails complets → GET /activites/{id}/detail
    """
    q = db.query(Activite).options(joinedload(Activite.categorie))
    if actif_seulement:
        q = q.filter(Activite.actif == True)
    if categorie_id:
        q = q.filter(Activite.categorie_id == categorie_id)
    return q.order_by(Activite.nom).all()


# ── GET /activites/{id} ───────────────────────────────────────────

@router.get("/{aid}", response_model=ActiviteOut)
def get_un(aid: int, db: Session = Depends(get_db)):
    """Fiche légère d'une activité. Pour les stats complètes → /{id}/detail"""
    return _load(db, aid)


# ── GET /activites/{id}/detail ────────────────────────────────────

@router.get("/{aid}/detail", response_model=ActiviteDetailOut)
def get_detail(aid: int, db: Session = Depends(get_db)):
    """
    Fiche complète d'une activité avec toutes ses statistiques :

      • CA total cumulé (toutes dates)
      • CA semaine courante et mois courant
      • Nombre de sessions (total + 30 derniers jours)
      • Liste de tous les commerçants ayant exercé cette activité
        avec leur CA individuel et nombre de sessions
      • Historique des 10 dernières sessions
    """
    activite = (db.query(Activite)
                .options(joinedload(Activite.categorie))
                .filter(Activite.id == aid)
                .first())
    if not activite:
        raise HTTPException(404, "Activité non trouvée")

    today   = date.today()
    d30     = today - timedelta(days=30)
    d_sem0  = calcul_service.debut_semaine(today)
    d_sem1  = calcul_service.fin_semaine(today)
    d_mois0 = calcul_service.debut_mois(today)
    d_mois1 = calcul_service.fin_mois(today)

    # ── CA ───────────────────────────────────────────────────────
    ca_total = calcul_service.ca_periode(db, aid, date(2000, 1, 1), today) or 0.0
    ca_semaine = calcul_service.ca_periode(db, aid, d_sem0, d_sem1) or 0.0
    ca_mois    = calcul_service.ca_periode(db, aid, d_mois0, d_mois1) or 0.0

    # ── Sessions ─────────────────────────────────────────────────
    # Sessions FERMÉES = sessions ayant contribué au CA (recette déclarée)
    # Les sessions ouvertes en cours ne sont pas encore comptabilisées
    nb_sessions_total = (
        db.query(func.count(SessionJournaliere.id))
        .filter(SessionJournaliere.activite_id == aid,
                SessionJournaliere.statut == StatutSessionEnum.FERME)
        .scalar() or 0
    )
    nb_sessions_30j = (
        db.query(func.count(SessionJournaliere.id))
        .filter(SessionJournaliere.activite_id == aid,
                SessionJournaliere.statut == StatutSessionEnum.FERME,
                SessionJournaliere.date_session >= d30)
        .scalar() or 0
    )

    # ── Commerçants actifs sur cette activité ────────────────────
    # Un commerçant est "actif" sur cette activité s'il a au moins
    # une session ouverte avec cet activite_id.
    # Commerçants ayant au moins une session FERMÉE sur cette activité
    # (i.e. ayant réellement contribué au CA)
    commercants_ids_rows = (
        db.query(func.distinct(SessionJournaliere.commercant_id))
        .filter(SessionJournaliere.activite_id == aid,
                SessionJournaliere.statut == StatutSessionEnum.FERME)
        .all()
    )
    commercants_ids = [r[0] for r in commercants_ids_rows]

    commercants_detail: list[CommercantActiviteOut] = []

    for cid in commercants_ids:
        com = (db.query(Commercant)
               .options(joinedload(Commercant.zone_principale))
               .filter(Commercant.id == cid)
               .first())
        if not com:
            continue

        # Nombre de sessions FERMÉES de CE commerçant SUR CETTE activité
        nb_ses = (
            db.query(func.count(SessionJournaliere.id))
            .filter(SessionJournaliere.commercant_id == cid,
                    SessionJournaliere.activite_id   == aid,
                    SessionJournaliere.statut == StatutSessionEnum.FERME)
            .scalar() or 0
        )

        # CA total de CE commerçant SUR CETTE activité (sessions FERMÉES uniquement)
        ca_com = (
            db.query(func.coalesce(func.sum(SessionJournaliere.recette_journaliere), 0.0))
            .filter(SessionJournaliere.commercant_id == cid,
                    SessionJournaliere.activite_id   == aid,
                    SessionJournaliere.statut == StatutSessionEnum.FERME,
                    SessionJournaliere.recette_journaliere.isnot(None))
            .scalar() or 0.0
        )

        # Dernière session FERMÉE de CE commerçant SUR CETTE activité
        derniere = (
            db.query(func.max(SessionJournaliere.date_session))
            .filter(SessionJournaliere.commercant_id == cid,
                    SessionJournaliere.activite_id   == aid,
                    SessionJournaliere.statut == StatutSessionEnum.FERME)
            .scalar()
        )

        commercants_detail.append(CommercantActiviteOut(
            id              = com.id,
            telephone       = com.telephone,
            nom_commercial  = com.nom_commercial,
            type_presence   = com.type_presence,
            zone_principale = com.zone_principale,
            nb_sessions     = nb_ses,
            ca_total        = float(ca_com),
            derniere_session= derniere,
        ))

    # Trier par CA décroissant
    commercants_detail.sort(key=lambda c: c.ca_total, reverse=True)

    # ── 10 dernières sessions ────────────────────────────────────
    sessions_brutes = (
        db.query(SessionJournaliere)
        .options(
            joinedload(SessionJournaliere.commercant),
            joinedload(SessionJournaliere.zone_observation),
        )
        .filter(SessionJournaliere.activite_id == aid)
        .order_by(SessionJournaliere.date_session.desc())
        .limit(10)
        .all()
    )

    sessions_recentes = [
        SessionRecente(
            id                  = s.id,
            date_session        = s.date_session,
            statut              = s.statut,
            recette_journaliere = s.recette_journaliere,
            commercant_nom      = (s.commercant.nom_commercial or s.commercant.telephone
                                   if s.commercant else None),
            zone_nom            = (s.zone_observation.nom if s.zone_observation else None),
        )
        for s in sessions_brutes
    ]

    # ── Assemblage ───────────────────────────────────────────────
    return ActiviteDetailOut(
        id                        = activite.id,
        nom                       = activite.nom,
        categorie                 = activite.categorie,
        mots_cles                 = activite.mots_cles,
        date_premiere_observation = activite.date_premiere_observation,
        actif                     = activite.actif,
        created_at                = activite.created_at,
        ca_total                  = ca_total,
        ca_semaine                = ca_semaine,
        ca_mois                   = ca_mois,
        nb_sessions_total         = nb_sessions_total,
        nb_sessions_30j           = nb_sessions_30j,
        nb_commercants            = len(commercants_detail),
        commercants               = commercants_detail,
        sessions_recentes         = sessions_recentes,
    )


# ── POST /activites/ ──────────────────────────────────────────────

@router.post("/", response_model=ActiviteOut, status_code=201)
async def creer(payload: ActiviteCreate, db: Session = Depends(get_db)):
    if not db.get(CategorieActivite, payload.categorie_id):
        raise HTTPException(404, "Catégorie non trouvée")
    if db.query(Activite).filter(Activite.nom.ilike(payload.nom)).first():
        raise HTTPException(409, f"Activité '{payload.nom}' existe déjà.")

    a = Activite(**payload.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    a = _load(db, a.id)
    await alerte_service.traiter_nouvelle_activite(db, a)
    return a


# ── PATCH /activites/{id} ─────────────────────────────────────────

@router.patch("/{aid}", response_model=ActiviteOut)
def modifier(aid: int, payload: ActiviteUpdate, db: Session = Depends(get_db)):
    a = db.get(Activite, aid)
    if not a:
        raise HTTPException(404, "Activité non trouvée")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    return _load(db, aid)


# ── PATCH /activites/{id}/activer ────────────────────────────────

@router.patch("/{aid}/activer", response_model=ActiviteOut)
def activer(aid: int, actif: bool = True, db: Session = Depends(get_db)):
    a = db.get(Activite, aid)
    if not a:
        raise HTTPException(404, "Activité non trouvée")
    a.actif = actif; db.commit()
    return _load(db, aid)