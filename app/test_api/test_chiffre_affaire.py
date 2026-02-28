from app.database import SessionLocal
from app.models.models import Activite, SessionJournaliere
from datetime import date

db = SessionLocal()

# Fetch all activities with their CA
activities_with_ca = db.query(Activite.nom, Activite.chiffre_affaire).all()

for nom, ca in activities_with_ca:
    print(f"Activité: {nom}, Chiffre d'affaire: {ca} FCFA")

def test_chiffre_affaire():
    db = SessionLocal()

    # Create test data
    activite = Activite(nom="Test Activite", categorie_id=1, date_premiere_observation=date.today())
    db.add(activite)
    db.flush()

    session1 = SessionJournaliere(
        activite_id=activite.id,
        date_session=date(2023, 10, 1),
        statut="ouvert",
        recette_journaliere=10000,
    )
    session2 = SessionJournaliere(
        activite_id=activite.id,
        date_session=date(2023, 10, 2),
        statut="ouvert",
        recette_journaliere=15000,
    )
    db.add_all([session1, session2])
    db.commit()

    # Test Python calculation
    assert activite.chiffre_affaire == 25000

    # Test SQL calculation
    result = db.query(Activite.chiffre_affaire).filter(Activite.id == activite.id).scalar()
    assert result == 25000