"""
models.py — 9 tables SQLAlchemy — Veille Marché Dang
Commune de Ngaoundéré 3ème — Région Adamaoua

═══════════════════════════════════════════════════════════════════
LOGIQUE MÉTIER DES SESSIONS ET CA — LIRE ATTENTIVEMENT
═══════════════════════════════════════════════════════════════════

  SESSION JOURNALIÈRE — la règle fondamentale
  ───────────────────────────────────────────────────────────────
  • 1 session = 1 JOURNÉE de travail d'UN commerçant.
  • recette_journaliere = total FCFA déclaré pour TOUTE sa journée.
    L'agent saisit "Mama Bawa a fait 15 000 FCFA aujourd'hui".
    Ce n'est PAS une transaction individuelle, c'est le bilan du jour.
  • UNIQUE(commercant_id, date_session) → un commerçant a exactement
    1 bilan par jour. Correct et voulu.

  COMMENT LE CA S'ACCUMULE
  ───────────────────────────────────────────────────────────────
  Le CA ne s'accumule PAS via plusieurs sessions dans la même journée.
  Il s'accumule via des sessions sur des JOURS DIFFÉRENTS.

    Mama Bawa (Vente maïs) :
      Lundi    → session  →  15 000 FCFA
      Mardi    → session  →  12 000 FCFA
      Mercredi → session  →  18 000 FCFA
                              ──────────
    CA "Vente maïs" cette semaine = 45 000 FCFA (3 sessions, 3 jours)

    Salihou (aussi Vente maïs) :
      Lundi    → session  →  20 000 FCFA
      Mercredi → session  →  17 000 FCFA
                              ──────────
    CA "Vente maïs" cette semaine = 45 000 + 37 000 = 82 000 FCFA

  → Plusieurs commerçants × plusieurs jours = CA de l'activité.

  ACTIVITE_ID DANS LA SESSION
  ───────────────────────────────────────────────────────────────
  activite_id dans SessionJournaliere = activité exercée CE jour.
  Dérivé automatiquement depuis Commercant.activite_id à la création
  (plus besoin de le passer dans le POST body).
  Gardé explicitement pour les semi-sédentaires qui peuvent changer
  d'activité selon le jour.

  CA PAR ACTIVITÉ (hybrid_property + calcul_service)
  ───────────────────────────────────────────────────────────────
  Activite.ca_total = SUM des recettes de toutes les sessions ouvertes
  dont activite_id = cet activite, sur toutes les dates.
  → PAS de colonne stockée, calculé dynamiquement.

  CA PAR CATÉGORIE (calcul_service.ca_categorie)
  CA GLOBAL DU MARCHÉ (calcul_service.ca_global)
  → Tous calculés à partir des sessions, jamais stockés.
═══════════════════════════════════════════════════════════════════
"""

import enum
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    Date, DateTime, Text, Enum, ForeignKey, UniqueConstraint, select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ═══════════════════════════════════════
# ÉNUMÉRATIONS
# ═══════════════════════════════════════

class TypePresenceEnum(str, enum.Enum):
    SEDENTAIRE      = "sedentaire"
    AMBULANT        = "ambulant"
    SEMI_SEDENTAIRE = "semi_sedentaire"

class StatutSessionEnum(str, enum.Enum):
    OUVERT                   = "ouvert"
    FERME                    = "ferme"
    ABSENT_MALADIE           = "absent_maladie"
    ABSENT_APPROVISIONNEMENT = "absent_approvisionnement"
    INCONNU                  = "inconnu"

class PeriodeEnum(str, enum.Enum):
    JOUR    = "jour"
    SEMAINE = "semaine"
    MOIS    = "mois"

class TendanceEnum(str, enum.Enum):
    HAUSSE = "hausse"
    BAISSE = "baisse"
    STABLE = "stable"

class TypeAlerteEnum(str, enum.Enum):
    NOUVELLE_ACTIVITE  = "nouvelle_activite"
    DECLIN_ACTIVITE    = "declin_activite"
    COMMERCANT_INACTIF = "commercant_inactif"
    PIC_CA             = "pic_ca"
    AMBULANT_DISPARU   = "ambulant_disparu"
    MOT_CLE_MATCH      = "mot_cle_match"

class NiveauAlerteEnum(str, enum.Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITIQUE = "critique"


# ═══════════════════════════════════════
# 1. CATEGORIE_ACTIVITE
# ═══════════════════════════════════════

class CategorieActivite(Base):
    __tablename__ = "categories_activite"

    id          = Column(Integer, primary_key=True, index=True)
    nom         = Column(String(80),  nullable=False, unique=True, index=True)
    description = Column(Text,        nullable=True)
    icone       = Column(String(10),  nullable=True)
    actif       = Column(Boolean,     nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    activites = relationship("Activite", back_populates="categorie")


# ═══════════════════════════════════════
# 2. ACTIVITE
# ═══════════════════════════════════════

class Activite(Base):
    """
    Type précis de commerce exercé sur le marché de Dang.

    ca_total (hybrid_property) :
        SUM(recette_journaliere) de toutes les sessions ouvertes
        dont activite_id = cet activite, sur toutes les dates.
        Calculé dynamiquement — JAMAIS stocké en colonne.
    """
    __tablename__ = "activites"

    id                        = Column(Integer, primary_key=True, index=True)
    categorie_id              = Column(Integer, ForeignKey("categories_activite.id"), nullable=False)
    nom                       = Column(String(150), nullable=False, index=True)
    mots_cles                 = Column(Text,        nullable=True)
    date_premiere_observation = Column(Date,        nullable=False)
    actif                     = Column(Boolean,     nullable=False, default=True)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())

    categorie   = relationship("CategorieActivite",  back_populates="activites")
    sessions    = relationship("SessionJournaliere", back_populates="activite")
    indicateurs = relationship("IndicateurActivite", back_populates="activite")
    commercants = relationship("Commercant",          back_populates="activite")
    alertes     = relationship("Alerte",             back_populates="activite")
    suivis      = relationship("SuiviActivite",      back_populates="activite")

    @hybrid_property
    def ca_total(self) -> float:
        """
        CA cumulé de cette activité — somme de TOUTES ses sessions ouvertes.
        S'utilise quand les sessions sont déjà chargées en mémoire (joinedload).
        """
        return sum(
            s.recette_journaliere
            for s in self.sessions
            if s.statut == StatutSessionEnum.OUVERT
            and s.recette_journaliere is not None
        )

    @ca_total.expression
    def ca_total(cls):
        """Version SQL — pour ORDER BY et filtres sans charger les sessions."""
        return (
            select(func.coalesce(func.sum(SessionJournaliere.recette_journaliere), 0.0))
            .where(SessionJournaliere.activite_id == cls.id)
            .where(SessionJournaliere.statut == StatutSessionEnum.OUVERT)
            .correlate(cls)
            .scalar_subquery()
        )


# ═══════════════════════════════════════
# 3. ZONE_MARCHE
# ═══════════════════════════════════════

class ZoneMarche(Base):
    __tablename__ = "zones_marche"

    id          = Column(Integer, primary_key=True, index=True)
    nom         = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text,        nullable=True)
    latitude    = Column(Float,       nullable=True)
    longitude   = Column(Float,       nullable=True)
    actif       = Column(Boolean,     nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    commercants_principaux = relationship("Commercant",         back_populates="zone_principale")
    sessions               = relationship("SessionJournaliere", back_populates="zone_observation")


# ═══════════════════════════════════════
# 4. COMMERCANT
# ═══════════════════════════════════════

class Commercant(Base):
    """
    Commerçant identifié sur le marché de Dang.

    activite_id : activité PRINCIPALE du commerçant.
        Utilisée par défaut lors de la création de session
        (l'agent n'a pas à la ressaisir chaque jour).
        Un semi-sédentaire peut exercer une activité différente
        certains jours → activite_id dans SessionJournaliere
        peut alors différer de ce champ.
    """
    __tablename__ = "commercants"

    id                 = Column(Integer, primary_key=True, index=True)
    activite_id        = Column(Integer, ForeignKey("activites.id"),    nullable=False)
    zone_principale_id = Column(Integer, ForeignKey("zones_marche.id"), nullable=True)
    telephone          = Column(String(20),  nullable=False, unique=True, index=True)
    nom_commercial     = Column(String(150), nullable=True)
    type_presence      = Column(Enum(TypePresenceEnum), nullable=False,
                                default=TypePresenceEnum.SEDENTAIRE)
    zones_circuit      = Column(Text,        nullable=True)
    point_depart       = Column(String(200), nullable=True)
    date_premiere_obs  = Column(Date,        nullable=False)
    actif              = Column(Boolean,     nullable=False, default=True)
    notes              = Column(Text,        nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    activite        = relationship("Activite",           back_populates="commercants")
    zone_principale = relationship("ZoneMarche",         back_populates="commercants_principaux")
    sessions        = relationship("SessionJournaliere", back_populates="commercant")
    alertes         = relationship("Alerte",             back_populates="commercant")


# ═══════════════════════════════════════
# 5. SESSION_JOURNALIERE
# ═══════════════════════════════════════

class SessionJournaliere(Base):
    """
    Bilan quotidien d'UN commerçant — grain de base de toute la collecte.

    ═══════════════════════════════════════════════════════════════
    RÈGLE : 1 SESSION PAR COMMERÇANT PAR JOUR
    ═══════════════════════════════════════════════════════════════
    UNIQUE(commercant_id, date_session).

    L'agent passe en tournée, remplit une fiche papier par commerçant.
    Le soir, il rentre et saisit le bilan de la journée pour chaque
    commerçant : "Mama Bawa a fait 15 000 FCFA aujourd'hui".

    recette_journaliere est le TOTAL de la journée entière de ce
    commerçant. Pas une transaction, un bilan global.
      → Lundi   : session → 15 000 FCFA
      → Mardi   : session → 12 000 FCFA   ← nouvelle session, nouveau jour
      → Mercredi: session → 18 000 FCFA   ← nouvelle session, nouveau jour
    Le CA de l'activité = somme de ces sessions sur la période.

    ═══════════════════════════════════════════════════════════════
    ACTIVITE_ID — dérivé automatiquement, rarement à surcharger
    ═══════════════════════════════════════════════════════════════
    À la création (POST /sessions/), si activite_id n'est pas fourni,
    le router le copie depuis Commercant.activite_id automatiquement.
    Un semi-sédentaire qui change d'activité ce jour-là peut fournir
    un activite_id différent explicitement.

    ═══════════════════════════════════════════════════════════════
    STATUT ET RECETTE
    ═══════════════════════════════════════════════════════════════
    statut=ouvert   → recette_journaliere obligatoire (> 0)
    statut≠ouvert   → recette_journaliere NULL
    (absent maladie, absent approvisionnement, fermé, inconnu)

    score_fiabilite :
        0.90 → fiche papier vérifiée par l'agent (source principale)
        0.65 → déclaration orale non vérifiée
        0.40 → estimation sans fiche
    """
    __tablename__ = "sessions_journalieres"

    id                  = Column(Integer, primary_key=True, index=True)
    commercant_id       = Column(Integer, ForeignKey("commercants.id"),  nullable=False)
    activite_id         = Column(Integer, ForeignKey("activites.id"),    nullable=False)
    zone_observation_id = Column(Integer, ForeignKey("zones_marche.id"), nullable=True)
    date_session        = Column(Date,    nullable=False, index=True)
    statut              = Column(Enum(StatutSessionEnum), nullable=False,
                                 default=StatutSessionEnum.OUVERT)
    recette_journaliere = Column(Float,   nullable=True)
    score_fiabilite     = Column(Float,   nullable=False, default=0.90)
    notes               = Column(Text,    nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("commercant_id", "date_session",
                         name="uq_session_commercant_date"),
    )

    commercant       = relationship("Commercant",  back_populates="sessions")
    activite         = relationship("Activite",    back_populates="sessions")
    zone_observation = relationship("ZoneMarche",  back_populates="sessions")


# ═══════════════════════════════════════
# 6. INDICATEUR_ACTIVITE
# ═══════════════════════════════════════

class IndicateurActivite(Base):
    """
    Agrégats précalculés par activité et par période.

    ca_total = SUM(recette_journaliere) de toutes les sessions ouvertes
               de cette activité entre date_debut et date_fin.
               = plusieurs commerçants × plusieurs jours.
    """
    __tablename__ = "indicateurs_activite"

    id                = Column(Integer, primary_key=True, index=True)
    activite_id       = Column(Integer, ForeignKey("activites.id"), nullable=False)
    periode           = Column(Enum(PeriodeEnum), nullable=False)
    date_debut        = Column(Date,    nullable=False)
    date_fin          = Column(Date,    nullable=False)
    ca_total          = Column(Float,   nullable=True)
    nb_commercants    = Column(Integer, nullable=True)
    taux_presence_moy = Column(Float,   nullable=True)
    tendance          = Column(Enum(TendanceEnum), nullable=True)
    ecart_pct         = Column(Float,   nullable=True)
    calculated_at     = Column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("activite_id", "periode", "date_debut",
                         name="uq_indicateur_activite_periode"),
    )

    activite = relationship("Activite", back_populates="indicateurs")


# ═══════════════════════════════════════
# 7. ALERTE
# ═══════════════════════════════════════

class Alerte(Base):
    __tablename__ = "alertes"

    id            = Column(Integer, primary_key=True, index=True)
    type_alerte   = Column(Enum(TypeAlerteEnum),   nullable=False, index=True)
    activite_id   = Column(Integer, ForeignKey("activites.id"),   nullable=True)
    commercant_id = Column(Integer, ForeignKey("commercants.id"), nullable=True)
    niveau        = Column(Enum(NiveauAlerteEnum), nullable=False)
    message       = Column(Text,    nullable=False)
    lue           = Column(Boolean, nullable=False, default=False)
    push_envoyee  = Column(Boolean, nullable=False, default=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    activite   = relationship("Activite",   back_populates="alertes")
    commercant = relationship("Commercant", back_populates="alertes")


# ═══════════════════════════════════════
# 8. SUIVI_ACTIVITE
# ═══════════════════════════════════════

class SuiviActivite(Base):
    __tablename__ = "suivis_activite"

    id          = Column(Integer, primary_key=True, index=True)
    token_fcm   = Column(String(300), nullable=False, index=True)
    activite_id = Column(Integer, ForeignKey("activites.id"), nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token_fcm", "activite_id", name="uq_suivi_token_activite"),
    )

    activite = relationship("Activite", back_populates="suivis")


# ═══════════════════════════════════════
# 9. RECHERCHE_SAUVEGARDEE
# ═══════════════════════════════════════

class RechercheSauvegardee(Base):
    __tablename__ = "recherches_sauvegardees"

    id                 = Column(Integer, primary_key=True, index=True)
    token_fcm          = Column(String(300), nullable=False, index=True)
    mots_cles          = Column(String(300), nullable=False)
    derniere_alerte_at = Column(DateTime(timezone=True), nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token_fcm", "mots_cles", name="uq_recherche_token_motscles"),
    )