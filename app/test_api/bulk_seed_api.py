import sys
import os
from sqlalchemy.orm import Session
from datetime import date

# Adjust PATH for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.database import SessionLocal
from app.models.models import CategorieActivite, Activite, Commercant

def seed_database():
    db: Session = SessionLocal()
    try:
        print("🏗️  Initialisation des données pour le Marché de Dang...")

        # 1. Seed Categories
        categories_data = {
            "Alimentation": "🛒",
            "Restauration": "🍲",
            "Habillement": "👕",
            "Services & Divers": "🛠️",
            "Électronique & Digital": "📱",
            "Santé & Beauté": "🌿"
        }
        
        cat_ids = {}
        for nom, icone in categories_data.items():
            cat = db.query(CategorieActivite).filter_by(nom=nom).first()
            if not cat:
                cat = CategorieActivite(nom=nom, icone=icone, description=f"Commerce de type {nom}")
                db.add(cat)
                db.flush()  # Get the ID immediately
            cat_ids[nom] = cat.id

        # 2. Seed Activities
        activites_list = [
            ("Vente de Riz au sac", "Alimentation", "riz, sac, blanc, parfumé, céréale"),
            ("Commerce de Maïs", "Alimentation", "maïs, grain, sac, agriculture"),
            ("Vente de Condiments", "Alimentation", "sel, cube, tomate, oignon, épices"),
            ("Boucherie", "Alimentation", "viande, bœuf, mouton, kilo"),
            ("Vente d'Huile végétale", "Alimentation", "huile, litre, cuisine, friture"),
            ("Poissonnerie", "Alimentation", "poisson, frais, glacé, maquereau"),
            ("Vente de Farine", "Alimentation", "farine, blé, beignet, pâtisserie"),
            ("Beignets-Haricots (BH)", "Restauration", "petit-déjeuner, haricot, bouillie, friture"),
            ("Tourne-dos (Plats locaux)", "Restauration", "riz, sauce, gombo, couscous, déjeuner"),
            ("Cafétéria Étudiante", "Restauration", "café, pain, œuf, omelette, spaghetti"),
            ("Vente de Suya (Braises)", "Restauration", "viande, braisé, piment, soja, nuit"),
            ("Grillades de Poisson", "Restauration", "poisson, braisé, bar, carpe"),
            ("Débit de boisson (Vente de bière)", "Restauration", "bière, jus, bar, rafraîchissement"),
            ("Friperie (Vêtements usagés)", "Habillement", "habit, occasion, chemise, pantalon, fripe"),
            ("Vente de Tissus (Pagnes)", "Habillement", "pagne, wax, tissu, couture"),
            ("Atelier de Couture", "Habillement", "tailleur, mesure, robe, costume, réparation"),
            ("Vente de Chaussures", "Habillement", "soulier, basket, sandale, cuir"),
            ("Vente de Sacs à main", "Habillement", "sac, femme, voyage, cuir"),
            ("Quincaillerie", "Services & Divers", "ciment, clou, fer, construction, outil"),
            ("Vente de Charbon", "Services & Divers", "énergie, cuisine, bois, charbon"),
            ("Pressing & Lavanderie", "Services & Divers", "lavage, fer, habit, nettoyage"),
            ("Vente de Pièces détachées", "Services & Divers", "moto, vélo, moteur, mécanique"),
            ("Transfert d'argent / Callbox", "Électronique & Digital", "orange, mtn, crédit, retrait, dépôt"),
            ("Maintenance Informatique", "Électronique & Digital", "ordinateur, pc, logiciel, réparation"),
            ("Vente d'Accessoires Téléphone", "Électronique & Digital", "chargeur, vitre, coque, écouteur"),
            ("Secrétariat Bureautique", "Électronique & Digital", "photocopie, impression, saisie, étudiant"),
            ("Pharmacie de rue (Médicaments)", "Santé & Beauté", "santé, comprimé, soin, pharmacopée"),
            ("Salon de Coiffure Homme", "Santé & Beauté", "tondeuse, coupe, barbe, rasage"),
            ("Salon de Beauté Femme", "Santé & Beauté", "tresse, mèche, vernis, maquillage"),
            ("Vente de Cosmétiques", "Santé & Beauté", "lait, savon, parfum, crème")
        ]

        for nom_act, cat_nom, tags in activites_list:
            existing = db.query(Activite).filter_by(nom=nom_act).first()
            if not existing:
                new_act = Activite(
                    nom=nom_act,
                    categorie_id=cat_ids[cat_nom],
                    mots_cles=tags,
                    date_premiere_observation=date.today()
                )
                db.add(new_act)
                print(f"✅ Ajouté : {nom_act}")

        # 3. Seed Commercants (Example)
        commercants_list = [
            ("Jean Dupont", "SEDENTAIRE", "Vente de Riz au sac"),
            ("Marie Claire", "AMBULANT", "Commerce de Maïs"),
            ("Paul Durand", "SEMI_SEDENTAIRE", "Vente de Condiments")
        ]

        for nom, type_presence, activite_nom in commercants_list:
            activite = db.query(Activite).filter_by(nom=activite_nom).first()
            if activite:
                existing_commercant = db.query(Commercant).filter_by(nom=nom).first()
                if not existing_commercant:
                    new_commercant = Commercant(
                        nom=nom,
                        type_presence=type_presence,
                        activite_id=activite.id
                    )
                    db.add(new_commercant)
                    print(f"✅ Commerçant ajouté : {nom}")

        db.commit()
        print("\n✨ Succès : Données initiales prêtes pour les tests !")

    except Exception as e:
        print(f"❌ Erreur : {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()