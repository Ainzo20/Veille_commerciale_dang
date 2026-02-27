"""
models.py — 9 tables SQLAlchemy — Veille Marché Dang
Commune de Ngaoundéré 3ème — Région Adamaoua

═══════════════════════════════════════════════════════════════
LOGIQUE MÉTIER DES CHIFFRES D'AFFAIRES
═══════════════════════════════════════════════════════════════

  SESSION JOURNALIÈRE (grain de base)
  ─────────────────────────────────────────────────────────────
  • Un commerçant → 1 session MAX par jour (UNIQUE commercant_id + date_session).
  • Une session appartient à UNE activité (activite_id) — celle exercée CE jour.
  • recette_journaliere : FCFA déclarés pour ce commerçant ce jour-là.
    → NULL si statut ≠ ouvert.

  CA D'UNE ACTIVITÉ (Activite.ca_total)
  ─────────────────────────────────────────────────────────────
  • = SUM(recette_journaliere) de toutes les sessions ouvertes
    dont activite_id = cet activite.
  • Une activité peut avoir N sessions par jour : un commerçant différent
    par session. Chaque commerçant lié à cette activité contribue
    quotidiennement avec SA propre session.
  • Calculé DYNAMIQUEMENT par hybrid_property — jamais stocké en colonne.
    → Pas de dénormalisation, pas de désynchronisation possible.

  CA PAR CATÉGORIE (calcul_service)
  ─────────────────────────────────────────────────────────────
  • = SUM des ca_total de toutes les activités d'une CategorieActivite.
  • Aucune colonne en base — calculé à la demande dans calcul_service.ca_categorie().

  CA GLOBAL DU MARCHÉ (calcul_service)
  ─────────────────────────────────────────────────────────────
  • = SUM(recette_journaliere) de TOUTES les sessions ouvertes, toutes activités.
  • Calculé dans calcul_service.ca_global() — aucune colonne.

  INDICATEURS PRÉ-CALCULÉS (IndicateurActivite)
  ─────────────────────────────────────────────────────────────
  • ca_total par période (jour / semaine / mois) — UPSERT chaque nuit.
  • Permet le graphique d'évolution et le top-activités sans requêtes lourdes.

═══════════════════════════════════════════════════════════════
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
    """
    Référentiel des grandes familles de commerce.
    Ex: Alimentation, Textile, Restauration, Téléphonie, Artisanat.

    Le CA par catégorie est calculé à la demande (calcul_service.ca_categorie)
    comme la somme des ca_total de toutes les activités de la catégorie.
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

    ── CA de l'activité ──────────────────────────────────────────────────────
    Il n'y a PAS de colonne chiffre_affaire en base.

    Pourquoi : une activité peut avoir N commerçants, chacun avec sa propre
    session journalière. Stocker une somme dans la ligne Activite créerait
    une dénormalisation — il faudrait la mettre à jour à chaque INSERT,
    UPDATE ou DELETE de session, et elle serait désynchronisée dès la
    moindre correction de saisie.

    À la place :
      • Activite.ca_total (hybrid_property) — somme en Python si les sessions
        sont déjà chargées en mémoire (joinedload), ou sous-requête SQL sinon.
      • calcul_service.ca_periode(db, activite_id, d0, d1) — pour une période.
      • IndicateurActivite.ca_total — pré-calculé chaque nuit pour les dashboards.
    ──────────────────────────────────────────────────────────────────────────
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

    # ── CA dynamique ──────────────────────────────────────────────

    @hybrid_property
    def ca_total(self) -> float:
        """
        CA cumulé toutes périodes : SUM des recette_journaliere des sessions
        ouvertes liées à cette activité.

        Utilisé quand l'objet est déjà chargé avec joinedload(Activite.sessions).
        Pour des agrégats en masse, utiliser calcul_service.ca_periode().
        """
        return sum(
            s.recette_journaliere
            for s in self.sessions
            if s.statut == StatutSessionEnum.OUVERT
            and s.recette_journaliere is not None
        )

    @ca_total.expression
    def ca_total(cls):
        """
        Variante SQL — utilisable dans .filter() et ORDER BY sans charger les sessions.
        Ex : db.query(Activite).order_by(Activite.ca_total.desc()).all()
        """
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
    point_depart  : description du dépôt ou domicile de départ de l'ambulant.
    date_premiere_obs : date réelle du 1er relevé terrain.
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

    # Une seule déclaration de chaque relation — pas de doublon
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

    ── Règle 1 session par commerçant par jour ──────────────────────────────
    UNIQUE(commercant_id, date_session) : un commerçant ne peut avoir qu'une
    session déclarée par jour. Cela représente sa journée complète de travail.

    ── Plusieurs sessions par activité par jour ─────────────────────────────
    En revanche, une ACTIVITÉ peut totaliser N sessions le même jour, car
    elle regroupe plusieurs commerçants qui l'exercent en parallèle.
    Ex: 'Vente de maïs en gros' le 15/07 peut avoir 4 sessions (4 commerçants),
    chacune avec sa propre recette_journaliere. Le CA de l'activité ce jour-là
    est la somme de ces 4 recettes.

    ── activite_id vs activite principale du commerçant ─────────────────────
    activite_id ici = activité exercée CE JOUR, pas nécessairement l'activité
    principale du commerçant. Un semi-sédentaire peut changer d'activité
    selon le jour. C'est CE activite_id qui est agrégé dans les indicateurs.

    ── recette_journaliere ──────────────────────────────────────────────────
    Montant total FCFA déclaré pour la JOURNÉE ENTIÈRE de CE commerçant.
    NULL si statut ≠ ouvert (absent/fermé).
    Sédentaire : recette de l'emplacement fixe.
    Ambulant    : recette totale de toute la tournée (plusieurs zones).

    score_fiabilite :
        0.90 → agent avec fiche papier vérifiée (source principale)
        0.65 → déclaration non vérifiée / estimation
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
    recette_journaliere = Column(Float,   nullable=True)   # FCFA — NULL si absent/fermé
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

    ca_total     : SUM(recette_journaliere) des sessions ouvertes de CETTE activité
                   sur la période — toutes les sessions de tous les commerçants
                   qui ont exercé cette activité entre date_debut et date_fin.
    nb_commercants : COUNT DISTINCT(commercant_id) actifs sur la période
    taux_presence  : moyenne des taux individuels (jours_ouverts / jours_observés)
    tendance       : comparaison avec la période précédente
    ecart_pct      : (ca_N - ca_N1) / ca_N1 × 100

    UNIQUE(activite_id, periode, date_debut) → UPSERT idempotent.
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
    """
    Événement métier détecté automatiquement par alerte_service.

    type_alerte   : catégorie de l'événement (voir TypeAlerteEnum).
    activite_id   : nullable — présent pour les alertes sur une activité
                    (nouvelle_activite, declin_activite, pic_ca, mot_cle_match).
    commercant_id : nullable — présent pour les alertes sur un commerçant
                    (commercant_inactif, ambulant_disparu).
    Une alerte peut être liée aux deux (ex : commercant_inactif lie aussi
    l'activité du commerçant pour cibler les push notifications).

    lue          : False → badge rouge dans l'app mobile.
    push_envoyee : True si Firebase a été appelé avec succès.
    """
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
    """
    Abonnement d'un appareil mobile Flutter à une activité spécifique.

    Identifié par token_fcm (Firebase device token) — pas de compte utilisateur.
    Quand une alerte est créée pour activite_id, push_service notifie tous
    les appareils ayant un SuiviActivite pour cette activité.

    UNIQUE(token_fcm, activite_id) : pas de doublon.
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

    Quand une Activite est créée avec des mots-clés qui correspondent,
    push_service notifie automatiquement les appareils abonnés.

    derniere_alerte_at : horodatage du dernier match — évite les doublons.
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
