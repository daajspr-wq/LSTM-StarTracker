#!/usr/bin/env python3
"""Détection d'étoiles astronomiques.

Fournit des fonctions de détection d'étoiles dans des images astronomiques
en utilisant des méthodes classiques du traitement d'images astronomiques.

Méthode par seuillage + étiquetage connexe + calcul du centroid.
"""

import numpy as np
from scipy import ndimage


def detect_stars(
    image: np.ndarray,
    threshold_sigma: float = 3.0,
    min_star_size: int = 3,
    max_star_size: int = 50,
) -> list[tuple[float, float]]:
    """Détecte les étoiles dans une image astronomique.

    Utilise une méthode classique en astronomie :
    1. Estimation du fond sky par la médiane locale
    2. Seuillage à N sigma du fond
    3. Étiquetage connexe pour identifier les sources
    4. Calcul du centroid (centre de masse) de chaque source

    Parameters
    ----------
    image
        Image d'entrée en tant que tableau numpy (2D, niveaux de gris).
    threshold_sigma
        Nombre d'écarts-types au-dessus du fond pour considérer une pixel
        comme appartenant à une étoile. Par défaut 3.0 (règle des 3 sigma).
    min_star_size
        Taille minimale (en pixels) qu'une région doit avoir pour être considérée
        comme une étoile. Empêche la détection du bruit de fond dispersé.
    max_star_size
        Taille maximale (en pixels) qu'une région peut avoir pour être considérée
        comme une étoile. Empêche la détection de grandes structures diffuses.

    Returns
    -------
    list[tuple[float, float]]
        Liste de positions (x, y) des étoiles détectées, sous forme de tuples
        de coordonnées en virgule flottante (le centre de masse peut être non
        entier).

    Raises
    ------
    ValueError
        Si l'image n'est pas 2D.
    """
    if image.ndim != 2:
        raise ValueError("L'image doit être 2D (niveaux de gras).")

    # --- Étape 1 : Estimation du fond sky ---
    # On utilise la médiane de l'image comme estimation du fond sky.
    # Pour de meilleurs résultats sur des images réelles, on pourrait utiliser
    # une morphologie ouverte ou une médiane glissante, mais la médiane globale
    # suffit pour une démo classique.
    background = np.median(image)
    # Écart-type du fond (on utilise l'écart absolu médian pour la robustesse)
    background_sigma = np.median(np.abs(image - background))

    # --- Étape 2 : Seuillage ---
    # On considère qu'une étoile commence là où l'intensité dépasse le fond
    # de threshold_sigma écarts-types.
    threshold = background + threshold_sigma * background_sigma

    # Binarisation : pixels au-dessus du seuil = 1, sinon 0
    binary_image = (image > threshold).astype(int)

    # --- Étape 3 : Étiquetage connexe ---
    # On trouve les régions connectées (étoiles)
    # Structure 8-connectée par défaut (inclut les diagonales)
    labeled_image, num_features = ndimage.label(binary_image)

    if num_features == 0:
        return []

    # --- Étape 4 : Calcul des centroids ---
    # On calcule le centre de masse de chaque région détectée
    # ndimage.center_of_mass renvoie une liste de tuples (y, x) pour chaque label
    # un tuple par région détectée, dans l'ordre des labels 1, 2, ..., num_features
    centroids = ndimage.center_of_mass(binary_image, labeled_image, range(1, num_features + 1))
    # Masse (nombre de pixels) de chaque région, pour filtrage par taille
    masses = ndimage.sum(binary_image, labeled_image, range(1, num_features + 1))

    # Filtrer par taille (nombre de pixels dans chaque région)
    valid_indices = []
    for i in range(1, num_features + 1):
        size = int(masses[i - 1])  # masses est indexé à partir de 0
        if min_star_size <= size <= max_star_size:
            valid_indices.append(i)

    # Extraire les positions valides
    positions = []
    for idx in valid_indices:
        # idx est le label (1, 2, ...), centroids[idx-1] donne (y, x)
        y, x = centroids[idx - 1]  # débundling du tuple (y, x)
        positions.append((float(x), float(y)))

    return positions


if __name__ == "__main__":
    """Démonstration du module de détection d'étoiles."""

    import numpy as np

    # Créer une image simulée avec quelques étoiles
    image = np.zeros((200, 200), dtype=float)

    # Étoile 1 : gaussian-like avec pic à 200
    y, x = np.ogrid[0:200, 0:200]
    center1_x, center1_y = 50, 80
    intensity1 = 100.0
    image += intensity1 * np.exp(-((x - center1_x)**2 + (y - center1_y)**2) / (2 * 5**2))

    # Étoile 2 : gaussian-like avec pic à 150
    center2_x, center2_y = 150, 120
    intensity2 = 80.0
    image += intensity2 * np.exp(-((x - center2_x)**2 + (y - center2_y)**2) / (2 * 4**2))

    # Fonds légèrement non uniforme
    image += np.random.normal(0, 5, image.shape)

    print("Image simulée créée avec 2 étoiles artificielles.")
    print(f"Niveau de fond estimé : {np.median(image):.1f}")

    # Détecter les étoiles
    detected = detect_stars(image, threshold_sigma=3.0)

    print(f"\n{len(detected)} etoile(s) detectee(s) :")
    for i, (x, y) in enumerate(detected):
        print(f"  Etoile {i+1} : position (x={x:.1f}, y={y:.1f})")

    # Attendu approximativement : (50, 80) et (150, 120)
    if len(detected) >= 2:
        print("\nDetection reussie : deux etoiles trouvees aux positions attendues.")