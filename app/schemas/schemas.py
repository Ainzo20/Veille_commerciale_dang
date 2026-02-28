"""
schemas.py — Pydantic v2 — Veille Marché Dang
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from app.models.models import (
    TypePresenceEnum, StatutSessionEnum, ScoreSourceEnum, PeriodeEnum,
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
    Corps du POST /sessions/ — OUVERTURE ou saisie a posteriori.

    ═══════════════════════════════════════════════════════════════
    FLUX NORMAL (deux étapes)
    ═══════════════════════════════════════════════════════════════

    ÉTAPE 1 — Matin : ouvrir (pas de recette)
      POST /sessions/
      { "commercant_id": 1, "activite_id": 3, "date_session": "2026-03-05" }
      → statut = ouvert, recette = null
      → NE contribue PAS au CA

    ÉTAPE 2 — Soir : fermer avec recette
      PATCH /sessions/{id}
      { "statut": "ferme", "recette_journaliere": 15000, "score_fiabilite": 0.90 }
      → CA mis à jour immédiatement

    ═══════════════════════════════════════════════════════════════
    SAISIE A POSTERIORI (une seule requête)
    ═══════════════════════════════════════════════════════════════
    { "commercant_id": 1, "activite_id": 3, "date_session": "2026-03-01",
      "statut": "ferme", "recette_journaliere": 15000 }

    ═══════════════════════════════════════════════════════════════
    PLUSIEURS SESSIONS PAR JOUR — UN COMMERÇANT / PLUSIEURS ACTIVITÉS
    ═══════════════════════════════════════════════════════════════
    Un commerçant peut avoir plusieurs sessions le même jour
    à condition qu'elles concernent des activités différentes :
      Session 1 : (commercant=1, activite=1, date=2026-03-05) → maïs
      Session 2 : (commercant=1, activite=2, date=2026-03-05) → huile
    ✅ 2 sessions autorisées.
    ❌ (commercant=1, activite=1, date=2026-03-05) × 2 = ERREUR 409.

    ═══════════════════════════════════════════════════════════════
    SCORE DE FIABILITÉ
    ═══════════════════════════════════════════════════════════════
    Utiliser ScoreSourceEnum pour les valeurs standards :
      1.00 ticket_caisse    → reçu ou ticket scanné
      0.90 fiche_agent      → fiche papier vérifiée (défaut)
      0.85 carnet_ventes    → carnet du commerçant
      0.70 declaration_face → déclaration directe
      0.60 declaration_tel  → déclaration téléphonique
      0.50 declaration_tiers→ déclaration par un tiers
      0.40 estimation_agent → observation visuelle
      0.25 estimation_passe → basé sur historique
      0.10 estimation_faible→ très incertaine

    Valeur composite possible : saisir la moyenne pondérée si
    plusieurs sources ont été utilisées pour la même session.
    """
    commercant_id:       int
    activite_id:         Optional[int]     = None   # déduit de commercant.activite_id si absent
    zone_observation_id: Optional[int]     = None
    date_session:        date
    statut:              StatutSessionEnum = StatutSessionEnum.OUVERT
    recette_journaliere: Optional[float]   = Field(None, gt=0)
    score_fiabilite:     float             = Field(0.90, ge=0.0, le=1.0)
    notes:               Optional[str]     = None

    @model_validator(mode="after")
    def check_recette(self) -> "SessionCreate":
        # Ouverture normale → pas de recette attendue
        if self.statut == StatutSessionEnum.OUVERT and self.recette_journaliere is not None:
            raise ValueError(
                "Ne pas envoyer recette_journaliere à l'ouverture. "
                "La recette se saisit au moment de la fermeture (PATCH /{id})."
            )
        # Fermeture directe (a posteriori) → recette obligatoire
        if self.statut == StatutSessionEnum.FERME and self.recette_journaliere is None:
            raise ValueError(
                "recette_journaliere obligatoire pour statut=ferme. "
                "Si recette inconnue, utiliser statut=ouvert puis PATCH plus tard."
            )
        # Absence → pas de recette
        if self.statut not in (StatutSessionEnum.OUVERT, StatutSessionEnum.FERME):
            if self.recette_journaliere is not None:
                raise ValueError(
                    "recette_journaliere doit être null pour une session d'absence."
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


# ── SCORE FIABILITÉ ───────────────────────────────────────────────

class ScoreSourceOut(OrmBase):
    """Valeurs standards de score_fiabilite pour affichage dans l'UI."""
    valeur:      float
    cle:         str
    label:       str
    description: str

# Catalogue exposable via GET /sessions/scores
SCORES_FIABILITE = [
    ScoreSourceOut(valeur=1.00, cle="ticket_caisse",
                   label="Ticket de caisse",
                   description="Reçu ou ticket scanné — source la plus fiable"),
    ScoreSourceOut(valeur=0.90, cle="fiche_agent",
                   label="Fiche agent",
                   description="Fiche papier vérifiée physiquement par l'agent"),
    ScoreSourceOut(valeur=0.85, cle="carnet_ventes",
                   label="Carnet de ventes",
                   description="Carnet tenu par le commerçant lui-même"),
    ScoreSourceOut(valeur=0.70, cle="declaration_face",
                   label="Déclaration directe",
                   description="Déclaration verbale face à face"),
    ScoreSourceOut(valeur=0.60, cle="declaration_tel",
                   label="Déclaration téléphonique",
                   description="Déclaration par téléphone"),
    ScoreSourceOut(valeur=0.50, cle="declaration_tiers",
                   label="Déclaration par un tiers",
                   description="Information transmise par un voisin ou associé"),
    ScoreSourceOut(valeur=0.40, cle="estimation_agent",
                   label="Estimation agent",
                   description="Observation visuelle de l'agent sur le terrain"),
    ScoreSourceOut(valeur=0.25, cle="estimation_passe",
                   label="Estimation historique",
                   description="Extrapolé depuis des données passées similaires"),
    ScoreSourceOut(valeur=0.10, cle="estimation_faible",
                   label="Estimation incertaine",
                   description="Très partielle ou peu fiable"),
]


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
    
class CommercantActiviteOut(OrmBase):
    """
    Commerçant ayant exercé une activité, avec ses statistiques personnelles.
    Retourné dans ActiviteDetailOut.commercants.
    """
    id:               int
    telephone:        str
    nom_commercial:   Optional[str]
    type_presence:    TypePresenceEnum
    zone_principale:  Optional[ZoneSummary]
    nb_sessions:      int     # Nombre de sessions ouvertes sur cette activité
    ca_total:         float   # CA cumulé de ce commerçant sur cette activité
    derniere_session: Optional[date]   # Date de sa dernière session ouverte


class SessionRecente(OrmBase):
    """Aperçu d'une session pour l'historique dans ActiviteDetailOut."""
    id:                  int
    date_session:        date
    statut:              StatutSessionEnum
    recette_journaliere: Optional[float]
    commercant_nom:      Optional[str]   # nom_commercial ou téléphone
    zone_nom:            Optional[str]


class ActiviteDetailOut(OrmBase):
    """
    Fiche complète d'une activité — retournée par GET /activites/{id}/detail

    Exemple de réponse :
    {
      "id": 3,
      "nom": "Vente de maïs en gros",
      "categorie": {"id": 1, "nom": "Alimentation", "icone": "🌾"},
      "ca_total": 347000.0,         ← cumulé depuis le début
      "ca_semaine": 82000.0,        ← semaine ISO en cours
      "ca_mois": 195000.0,          ← mois calendaire en cours
      "nb_sessions_total": 47,
      "nb_sessions_30j": 18,
      "nb_commercants": 3,
      "commercants": [
        {
          "id": 1,
          "telephone": "677001001",
          "nom_commercial": "Mama Bawa",
          "type_presence": "sedentaire",
          "nb_sessions": 22,
          "ca_total": 198000.0,
          "derniere_session": "2025-07-16"
        },
        {
          "id": 4,
          "telephone": "677001004",
          "nom_commercial": "Salihou",
          "type_presence": "semi_sedentaire",
          "nb_sessions": 18,
          "ca_total": 112000.0,
          "derniere_session": "2025-07-15"
        },
        ...
      ],
      "sessions_recentes": [
        {"id": 87, "date_session": "2025-07-16", "statut": "ouvert",
         "recette_journaliere": 18000.0, "commercant_nom": "Mama Bawa", "zone_nom": "Secteur A"},
        ...
      ]
    }
    """
    # Infos de base
    id:                        int
    nom:                       str
    categorie:                 CategorieSummary
    mots_cles:                 Optional[str]
    date_premiere_observation: date
    actif:                     bool
    created_at:                datetime

    # Chiffres d'affaires
    ca_total:           float   # Cumulé toutes dates
    ca_semaine:         float   # Semaine ISO en cours
    ca_mois:            float   # Mois calendaire en cours

    # Sessions
    nb_sessions_total:  int     # Total sessions ouvertes (toutes dates)
    nb_sessions_30j:    int     # Sessions ouvertes sur les 30 derniers jours

    # Commerçants
    nb_commercants:     int                        # Nombre de commerçants distincts
    commercants:        list[CommercantActiviteOut]  # Triés par CA décroissant

    # Historique récent
    sessions_recentes:  list[SessionRecente]       # 10 dernières sessions ouvertes, triées par date_session décroissante