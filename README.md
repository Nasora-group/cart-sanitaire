# HealthMap Sénégal Pro

Prototype web de recherche et de contrôle qualité du répertoire des structures sanitaires du Sénégal.

## Fonctionnalités incluses
- Recherche textuelle sur nom, région, département et commune.
- Filtres par région, département, commune, type et statut.
- Tableau paginé des établissements.
- Fiche de correction des données et statut de vérification.
- Import CSV en ajout de lignes.
- Export CSV des résultats filtrés.
- Carte SVG interactive originale accessible depuis le menu « Carte interactive ».
- Chargement automatique des 2 568 points intégrés à la carte originale dans une base vide, en séparant coordonnées SVG et coordonnées GPS.
- Endpoint de santé `/health`.
- Configuration de déploiement Render via `render.yaml`.

## Lancement local
Python 3.10+ recommandé.

```bash
python -m venv .venv
# Windows : .venv\Scripts\activate
# macOS/Linux : source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Ouvrir http://127.0.0.1:5000.

## Importer les données
Au premier démarrage, les points embarqués dans `static/carte-sanitaire-originale.html` sont chargés automatiquement si la base est vide. Pour les imports manuels, ouvrir **Importer CSV** et charger un CSV nettoyé. Colonnes reconnues : `name` ou `nom`, `facility_type` ou `type`, `region`, `department` ou `departement`, `commune`, `source`.

L’import ajoute des lignes sans dédoublonnage automatique. Contrôlez le fichier avant l’import pour éviter les doublons.

## Limites à régler avant la production
- Cette première version utilise SQLite. Pour une utilisation multi-entreprises en production, migrer vers PostgreSQL.
- L’administration et l’import CSV ne sont pas encore protégés par authentification : ne pas exposer l’application publiquement avant sécurisation.
- Le jeu de données source reste à vérifier : 2 568 points initiaux, avec champs incomplets et doublons potentiels. Il ne constitue pas un inventaire officiel exhaustif.
- La carte HTML source est intégrée dans `static/carte-sanitaire-originale.html`. Les champs `svg_x/svg_y` sont distincts de `latitude/longitude` : ne pas les utiliser comme coordonnées GPS.
- Vérifier les droits de réutilisation commerciale des sources DTGC/OpenStreetMap avant commercialisation.
- Les données ne doivent pas être présentées comme exhaustives ou certifiées avant validation.

## Déploiement Render
Le fichier `render.yaml` prépare un service web. Ajouter un disque persistant ou migrer vers PostgreSQL avant de compter sur la persistance des données ; le système de fichiers d’un service web peut être éphémère.
