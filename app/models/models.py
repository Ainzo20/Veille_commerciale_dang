"""
models.py — 9 tables SQLAlchemy — Veille Marché Dang
Commune de Ngaoundéré 3ème — Région Adamaoua

FLUX DE DONNÉES :
  Backoffice web (saisie) → API → Base de données → App mobile (lecture/alertes)

  1. Un agent enregistre les Commercants et Activites dans le backoffice.
  2. Après chaque tournée terrain (fiche papier), il saisit les Sessions.
  3. L'API calcule les Indicateurs et génère les Alertes automatiquement.
  4. L'app mobile Flutter lit tout en GET, suit des activités, sauvegarde des recherches.

TABLES :
  1  CategorieActivite     — familles de commerce (Alimentation, Textile, Restauration…)
  2  Activite              — types précis de commerce avec mots-clés indexés
  3  ZoneMarche            — secteurs géographiques du marché de Dang
  4  Commercant            — sédentaires / ambulants / semi-sédentaires
  5  SessionJournaliere    — recette déclarée par commerçant et par jour (cœur collecte)
  6  IndicateurActivite    — CA + tendances précalculés par activité et période
  7  Alerte                — événements détectés automatiquement
  8  SuiviActivite         — abonnement mobile à une activité (token_fcm)
  9  RechercheSauvegardee  — veille active sur mots-clés depuis mobile
"""

import enum
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    Date, DateTime, Text, Enum, ForeignKey, UniqueConstraint,
)
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
    """
    Référentiel des grandes familles de commerce.
    Ex: Alimentation, Textile, Restauration, Téléphonie, Artisanat.
    Administré dans le backoffice. Sert de filtre sur l'app mobile.
    """
    __tablename__ = "categories_activite"

    id          = Column(Integer, primary_key=True, index=True)
    nom         = Column(String(80),  nullable=False, unique=True, index=True)
    description = Column(Text,        nullable=True)
    icone       = Column(String(10),  nullable=True)   # emoji: 🌾 🍽️ 📱
    actif       = Column(Boolean,     nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    activites = relationship("Activite", back_populates="categorie")


# ═══════════════════════════════════════
# 2. ACTIVITE
# ═══════════════════════════════════════

class Activite(Base):
    """
    Type précis de commerce exercé sur le marché de Dang.
    Ex: 'Vente de maïs en gros', 'Restauration beignets/café', 'Recharge téléphone'.

    Créée UNIQUEMENT dans le backoffice web par un agent.
    → Déclenche immédiatement :
        - alerte type=nouvelle_activite
        - push notifications aux abonnés + aux recherches sauvegardées qui matchent

    mots_cles : chaîne libre indexée pour le moteur de recherche GET /recherche?q=
    date_premiere_observation : date réelle de la 1ère observation terrain
      (peut précéder created_at si l'agent saisit avec délai).
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
    commercants = relationship("Commercant",          back_populates="activite")
    sessions    = relationship("SessionJournaliere",  back_populates="activite")
    indicateurs = relationship("IndicateurActivite",  back_populates="activite")
    alertes     = relationship("Alerte",              back_populates="activite")
    suivis      = relationship("SuiviActivite",       back_populates="activite")


# ═══════════════════════════════════════
# 3. ZONE_MARCHE
# ═══════════════════════════════════════

class ZoneMarche(Base):
    """
    Secteur géographique identifié dans le marché de Dang.
    Ex: 'Secteur A — Céréales', 'Bord de route', 'Marché couvert', 'Zone nord'.

    latitude/longitude : réservés pour la carte interactive mobile (phase 2).
    Utilisé pour localiser les ambulants au moment de chaque session journalière.
    """
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
    Enregistré dans le backoffice web par un agent.

    telephone : identifiant fonctionnel UNIQUE — pas de nom complet (anonymat).

    type_presence :
        sedentaire      → emplacement fixe connu, zone_principale renseignée
        ambulant        → circule dans plusieurs zones, stock au dépôt/domicile
                          zones_circuit (JSON list) + point_depart recommandés
        semi_sedentaire → fixe certains jours, ambulant d'autres

    zones_circuit : JSON list des zones parcourues habituellement par un ambulant.
        Ex: '["Secteur A", "Bord de route", "Zone nord"]'
    point_depart : adresse/description du dépôt ou domicile de départ de l'ambulant.
    date_premiere_obs : date réelle du 1er relevé terrain (ancienneté du commerçant).
    """
    __tablename__ = "commercants"

    id                 = Column(Integer, primary_key=True, index=True)
    activite_id        = Column(Integer, ForeignKey("activites.id"),    nullable=False)
    zone_principale_id = Column(Integer, ForeignKey("zones_marche.id"), nullable=True)
    telephone          = Column(String(20),  nullable=False, unique=True, index=True)
    nom_commercial     = Column(String(150), nullable=True)
    type_presence      = Column(Enum(TypePresenceEnum), nullable=False,
                                default=TypePresenceEnum.SEDENTAIRE)
    zones_circuit      = Column(Text,        nullable=True)   # JSON list en texte
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
    Observation quotidienne d'un commerçant — cœur de la collecte.

    Saisie dans le backoffice web par un agent APRÈS sa tournée terrain.
    Le protocole : agent descend avec fiche papier structurée → rentre au bureau
    → saisit les fiches une à une dans le formulaire web.

    recette_journaliere : montant total en FCFA déclaré pour la journée.
        - Sédentaire : recette de l'emplacement fixe.
        - Ambulant   : recette totale de toute la tournée (plusieurs zones).
        NULL si statut ≠ ouvert (commerçant absent/fermé).

    activite_id : activité exercée CE JOUR — peut différer de l'activité principale
        (utile pour les semi-sédentaires qui changent d'activité selon les jours).

    zone_observation_id : zone où l'agent a croisé le commerçant ce jour.
        Obligatoire pour les ambulants. Pour sédentaires : déduit de zone_principale.

    score_fiabilite :
        0.90 → agent avec fiche papier vérifiée (source principale)
        0.65 → déclaration non vérifiée / estimation
        0.40 → estimation sans fiche

    CONTRAINTE UNIQUE (commercant_id, date_session) : un seul relevé par jour par
        commerçant. Idempotent — re-soumettre la même fiche retourne HTTP 409.
    """
    __tablename__ = "sessions_journalieres"

    id                  = Column(Integer, primary_key=True, index=True)
    commercant_id       = Column(Integer, ForeignKey("commercants.id"),   nullable=False)
    activite_id         = Column(Integer, ForeignKey("activites.id"),     nullable=False)
    zone_observation_id = Column(Integer, ForeignKey("zones_marche.id"),  nullable=True)
    date_session        = Column(Date,    nullable=False, index=True)
    statut              = Column(Enum(StatutSessionEnum), nullable=False,
                                 default=StatutSessionEnum.OUVERT)
    recette_journaliere = Column(Float,   nullable=True)   # FCFA
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
    Recalculés chaque nuit (cron POST /indicateurs/recalculer) ou à la demande.

    ca_total        : SUM(recette_journaliere) des sessions ouvertes sur la période
    nb_commercants  : COUNT DISTINCT commerçants avec au moins 1 session ouverte
    taux_presence   : moyenne des taux de présence individuels (jours_ouverts / jours_observés)
    tendance        : HAUSSE / BAISSE / STABLE comparé à la période précédente
    ecart_pct       : (ca_N - ca_N1) / ca_N1 × 100

    UNIQUE(activite_id, periode, date_debut) : idempotent — recalculer ne crée pas de doublons.
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
    calculated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("activite_id", "periode", "date_debut",
                         name="uq_indicateur_activite_periode"),
    )

    activite = relationship("Activite", back_populates="indicateurs")


# ═══════════════════════════════════════
# 7. ALERTE
# ═══════════════════════════════════════

class Alerte(Base):
    """
    Journal des événements détectés automatiquement par alerte_service.
    Jamais créée manuellement — toujours déclenchée par le code.

    TYPE → DÉCLENCHEUR :
        nouvelle_activite  → immédiatement après POST /activites/
        declin_activite    → CA en baisse 3 semaines consécutives (≥ SEUIL_DECLIN_PCT)
        commercant_inactif → sédentaire absent > SEUIL_INACTIF_JOURS jours
        ambulant_disparu   → ambulant absent > SEUIL_AMBULANT_DISPARU jours
        pic_ca             → CA journalier > SEUIL_PIC_MULTIPLICATEUR × moyenne
        mot_cle_match      → nouvelle activité correspond à une recherche sauvegardée

    lue          : passé à True par PATCH /alertes/{id}/lue depuis l'app mobile.
    push_envoyee : True quand Firebase a confirmé l'envoi de la notification.
    """
    __tablename__ = "alertes"

    id            = Column(Integer, primary_key=True, index=True)
    type_alerte   = Column(Enum(TypeAlerteEnum),   nullable=False, index=True)
    activite_id   = Column(Integer, ForeignKey("activites.id"),   nullable=True)
    commercant_id = Column(Integer, ForeignKey("commercants.id"), nullable=True)
    niveau        = Column(Enum(NiveauAlerteEnum), nullable=False, default=NiveauAlerteEnum.INFO)
    message       = Column(Text,    nullable=False)
    lue           = Column(Boolean, nullable=False, default=False)
    push_envoyee  = Column(Boolean, nullable=False, default=False)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    activite   = relationship("Activite",   back_populates="alertes")
    commercant = relationship("Commercant", back_populates="alertes")


# ═══════════════════════════════════════
# 8. SUIVI_ACTIVITE
# ═══════════════════════════════════════

class SuiviActivite(Base):
    """
    Abonnement d'un appareil mobile Flutter à une activité spécifique.

    Pas de compte utilisateur en phase 1 — identifié par token_fcm (Firebase device token).
    Quand une alerte est créée pour activite_id, push_service envoie une notification
    ciblée à tous les appareils ayant un SuiviActivite pour cette activité.

    Cas d'usage : un décideur municipal veut être alerté spécifiquement si l'activité
    'Vente de poisson fumé' est en déclin ou connaît un pic.

    UNIQUE(token_fcm, activite_id) : un appareil ne peut pas s'abonner deux fois
    à la même activité.
    """
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
    """
    Veille active sur mots-clés, enregistrée depuis l'application mobile.

    Cas d'usage : un décideur tape "pharmacopée plantes" dans la recherche
    et appuie sur 'Sauvegarder cette recherche'. Dès qu'une nouvelle activité
    est créée dans le backoffice avec des mots-clés correspondants (intersection
    de termes), il reçoit une push notification automatiquement.

    mots_cles         : chaîne brute saisie par l'utilisateur mobile.
    derniere_alerte_at: horodatage du dernier match — évite les doublons si
                        l'activité est modifiée plusieurs fois le même jour.

    UNIQUE(token_fcm, mots_cles) : pas de doublon pour le même appareil.
    """
    __tablename__ = "recherches_sauvegardees"

    id                 = Column(Integer, primary_key=True, index=True)
    token_fcm          = Column(String(300), nullable=False, index=True)
    mots_cles          = Column(String(300), nullable=False)
    derniere_alerte_at = Column(DateTime(timezone=True), nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token_fcm", "mots_cles", name="uq_recherche_token_motscles"),
    )
