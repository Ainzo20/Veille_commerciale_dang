"""
schemas.py — Pydantic v2 — Veille Marché Dang
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from app.models.models import (
    TypePresenceEnum, StatutSessionEnum, PeriodeEnum,
    TendanceEnum, TypeAlerteEnum, NiveauAlerteEnum,
)


class OrmBase(BaseModel):
    model_config = {"from_attributes": True}


# ── CATEGORIE ────────────────────────────────────────────────────

class CategorieCreate(OrmBase):
    nom:         str            = Field(..., min_length=2, max_length=80)
    description: Optional[str] = None
    icone:       Optional[str] = Field(None, max_length=10)

class CategorieUpdate(OrmBase):
    nom:         Optional[str]  = Field(None, min_length=2, max_length=80)
    description: Optional[str] = None
    icone:       Optional[str] = None
    actif:       Optional[bool] = None

class CategorieSummary(OrmBase):
    id: int; nom: str; icone: Optional[str]

class CategorieOut(OrmBase):
    id: int; nom: str; description: Optional[str]
    icone: Optional[str]; actif: bool; created_at: datetime


# ── ACTIVITE ─────────────────────────────────────────────────────

class ActiviteCreate(OrmBase):
    categorie_id:              int
    nom:                       str  = Field(..., min_length=2, max_length=150)
    mots_cles:                 Optional[str] = None
    date_premiere_observation: date

class ActiviteUpdate(OrmBase):
    nom:       Optional[str]  = Field(None, min_length=2, max_length=150)
    mots_cles: Optional[str]  = None
    actif:     Optional[bool] = None

class ActiviteSummary(OrmBase):
    id: int; nom: str; categorie: CategorieSummary; actif: bool

class ActiviteOut(OrmBase):
    id:                        int
    nom:                       str
    categorie:                 CategorieSummary
    mots_cles:                 Optional[str]
    date_premiere_observation: date
    # CA cumulé total : SUM des recette_journaliere de toutes les sessions
    # ouvertes liées à cette activité, sur toutes les dates.
    # Calculé dynamiquement (hybrid_property), pas stocké en colonne.
    ca_total:                  float
    actif:                     bool
    created_at:                datetime


# ── ZONE ─────────────────────────────────────────────────────────

class ZoneCreate(OrmBase):
    nom:         str             = Field(..., min_length=2, max_length=100)
    description: Optional[str]  = None
    latitude:    Optional[float] = None
    longitude:   Optional[float] = None

class ZoneUpdate(OrmBase):
    nom:         Optional[str]   = None
    description: Optional[str]  = None
    latitude:    Optional[float] = None
    longitude:   Optional[float] = None
    actif:       Optional[bool]  = None

class ZoneSummary(OrmBase):
    id: int; nom: str

class ZoneOut(OrmBase):
    id: int; nom: str; description: Optional[str]
    latitude: Optional[float]; longitude: Optional[float]
    actif: bool; created_at: datetime


# ── COMMERCANT ───────────────────────────────────────────────────

class CommercantCreate(OrmBase):
    activite_id:        int
    zone_principale_id: Optional[int]   = None
    telephone:          str              = Field(..., min_length=8, max_length=20)
    nom_commercial:     Optional[str]   = Field(None, max_length=150)
    type_presence:      TypePresenceEnum = TypePresenceEnum.SEDENTAIRE
    zones_circuit:      Optional[str]   = None
    point_depart:       Optional[str]   = Field(None, max_length=200)
    date_premiere_obs:  date
    notes:              Optional[str]   = None

    @model_validator(mode="after")
    def check_ambulant(self) -> "CommercantCreate":
        if self.type_presence == TypePresenceEnum.AMBULANT:
            if not self.zones_circuit and not self.point_depart:
                raise ValueError("Un ambulant nécessite zones_circuit ou point_depart.")
        return self

class CommercantUpdate(OrmBase):
    activite_id:        Optional[int]              = None
    zone_principale_id: Optional[int]              = None
    nom_commercial:     Optional[str]              = None
    type_presence:      Optional[TypePresenceEnum] = None
    zones_circuit:      Optional[str]              = None
    point_depart:       Optional[str]              = None
    actif:              Optional[bool]             = None
    notes:              Optional[str]              = None

class CommercantSummary(OrmBase):
    id: int; telephone: str; nom_commercial: Optional[str]
    type_presence: TypePresenceEnum; activite: ActiviteSummary

class CommercantOut(OrmBase):
    id: int; activite: ActiviteSummary
    zone_principale: Optional[ZoneSummary]
    telephone: str; nom_commercial: Optional[str]
    type_presence: TypePresenceEnum
    zones_circuit: Optional[str]; point_depart: Optional[str]
    date_premiere_obs: date; actif: bool
    notes: Optional[str]; created_at: datetime


# ── SESSION JOURNALIÈRE ───────────────────────────────────────────

class SessionCreate(OrmBase):
    """
    Corps du POST /sessions/.

    activite_id est OPTIONNEL :
      - Si absent → le router le déduit automatiquement de
        Commercant.activite_id (cas courant, 95% des saisies).
      - Si fourni → permet à un semi-sédentaire de déclarer une
        activité différente de son activité principale ce jour-là.

    La recette_journaliere est le TOTAL FCFA de la journée entière
    de ce commerçant (bilan global, pas une transaction unitaire).

    Exemples de saisie :
      Lundi   → {commercant_id:1, date_session:"2025-07-14", recette_journaliere:15000}
      Mardi   → {commercant_id:1, date_session:"2025-07-15", recette_journaliere:12000}
      Mercredi→ {commercant_id:1, date_session:"2025-07-16", recette_journaliere:18000}
    Ces 3 sessions génèrent un CA de 45 000 FCFA pour l'activité sur la semaine.
    """
    commercant_id:       int
    # activite_id optionnel : déduit de commercant.activite_id si absent
    activite_id:         Optional[int]     = None
    zone_observation_id: Optional[int]     = None
    date_session:        date
    statut:              StatutSessionEnum = StatutSessionEnum.OUVERT
    recette_journaliere: Optional[float]   = Field(None, gt=0)
    score_fiabilite:     float             = Field(0.90, ge=0.0, le=1.0)
    notes:               Optional[str]     = None

    @model_validator(mode="after")
    def check_recette(self) -> "SessionCreate":
        if self.statut == StatutSessionEnum.OUVERT and self.recette_journaliere is None:
            raise ValueError(
                "recette_journaliere est obligatoire quand statut=ouvert. "
                "Saisissez le total FCFA déclaré par le commerçant pour la journée."
            )
        if self.statut != StatutSessionEnum.OUVERT and self.recette_journaliere is not None:
            raise ValueError(
                "recette_journaliere doit être null si le commerçant est absent ou fermé."
            )
        return self

class SessionUpdate(OrmBase):
    statut:              Optional[StatutSessionEnum] = None
    recette_journaliere: Optional[float]             = Field(None, gt=0)
    zone_observation_id: Optional[int]               = None
    score_fiabilite:     Optional[float]             = Field(None, ge=0.0, le=1.0)
    notes:               Optional[str]               = None

class SessionSummary(OrmBase):
    id: int; date_session: date
    statut: StatutSessionEnum; recette_journaliere: Optional[float]

class SessionOut(OrmBase):
    id:                  int
    commercant:          CommercantSummary
    activite:            ActiviteSummary
    zone_observation:    Optional[ZoneSummary]
    date_session:        date
    statut:              StatutSessionEnum
    recette_journaliere: Optional[float]
    score_fiabilite:     float
    notes:               Optional[str]
    created_at:          datetime


# ── INDICATEUR ───────────────────────────────────────────────────

class IndicateurOut(OrmBase):
    id: int; activite: ActiviteSummary; periode: PeriodeEnum
    date_debut: date; date_fin: date
    # ca_total = SUM des recettes de toutes les sessions ouvertes
    # de cette activité entre date_debut et date_fin inclus.
    # Plusieurs commerçants × plusieurs jours.
    ca_total:          Optional[float]
    nb_commercants:    Optional[int]
    taux_presence_moy: Optional[float]
    tendance:          Optional[TendanceEnum]
    ecart_pct:         Optional[float]
    calculated_at:     datetime

class RecalculerRequest(OrmBase):
    activite_id: Optional[int]         = None
    periode:     Optional[PeriodeEnum] = None


# ── ALERTE ───────────────────────────────────────────────────────

class AlerteOut(OrmBase):
    id: int; type_alerte: TypeAlerteEnum
    activite:   Optional[ActiviteSummary]
    commercant: Optional[CommercantSummary]
    niveau: NiveauAlerteEnum; message: str
    lue: bool; push_envoyee: bool; created_at: datetime

class AlertePatchLue(OrmBase):
    lue: bool = True


# ── SUIVI ACTIVITE (mobile) ───────────────────────────────────────

class SuiviCreate(OrmBase):
    token_fcm:   str = Field(..., min_length=10, max_length=300)
    activite_id: int

class SuiviOut(OrmBase):
    id: int; token_fcm: str; activite: ActiviteSummary; created_at: datetime


# ── RECHERCHE SAUVEGARDEE (mobile) ────────────────────────────────

class RechercheCreate(OrmBase):
    token_fcm: str = Field(..., min_length=10, max_length=300)
    mots_cles: str = Field(..., min_length=2,  max_length=300)

class RechercheOut(OrmBase):
    id: int; token_fcm: str; mots_cles: str
    derniere_alerte_at: Optional[datetime]; created_at: datetime


# ── RECHERCHE FULL-TEXT ───────────────────────────────────────────

class ResultatRecherche(OrmBase):
    type:       str
    id:         int
    label:      str
    sous_label: Optional[str]
    score:      float

class ReponseRecherche(OrmBase):
    query:        str
    nb_resultats: int
    resultats:    list[ResultatRecherche]


# ── CA PAR CATÉGORIE ──────────────────────────────────────────────

class CaCategorieOut(OrmBase):
    """CA d'une catégorie sur une période — calculé à la demande."""
    categorie_id: int
    nom:          str
    icone:        Optional[str]
    ca:           float


# ── DASHBOARD ─────────────────────────────────────────────────────

class DashboardOut(OrmBase):
    nb_commercants_actifs:  int
    nb_activites_suivies:   int
    nb_zones:               int
    ca_semaine_courante:    Optional[float]
    ca_semaine_precedente:  Optional[float]
    tendance_globale:       Optional[TendanceEnum]
    ca_par_categorie:       list[CaCategorieOut]
    nb_alertes_non_lues:    int
    top_activites:          list[IndicateurOut]
    alertes_recentes:       list[AlerteOut]