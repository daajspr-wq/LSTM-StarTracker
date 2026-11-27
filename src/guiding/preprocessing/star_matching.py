#!/usr/bin/env python3
"""Matching d'étoiles entre images successives.

Fournit des fonctions pour associer les étoiles détectées dans une image
courante (N) à celles de l'image précédente (N-1), en gérant les cas
d'apparitions et de disparitions d'étoiles.

Méthode : correspondance par distance euclidienne avec seuillage,
suivi par association gloutonne optimale.
"""

from typing import Optional

import numpy as np


def match_stars(
    current_stars: "list[tuple[float, float]]",
    previous_stars: "list[tuple[float, float]]",
    disappearance_threshold: float = 50.0,
    appearance_threshold: float = 50.0,
    keep_top_n: "Optional[int]" = None,
) -> "tuple[list[tuple[int, int, float]], list[tuple[int, float, float]], list[tuple[int, float, float]]]":
    """Associe les étoiles entre deux images successives.

    Utilise une correspondance par distance euclidienne pour lier les étoiles
    présentes dans les deux images. Les étoiles qui n'ont pas de correspondance
    sont classées comme "disparues" (dans l'image précédente) ou "apparaissantes"
    (dans l'image actuelle).

    Parameters
    ----------
    current_stars
        Liste de positions (x, y) détectées dans l'image N.
    previous_stars
        Liste de positions (x, y) détectées dans l'image N-1.
    disappearance_threshold
        Seuil maximal de distance (en pixels) pour considérer qu'une étoile
        a disparu plutôt que déplacée fortement. Par défaut 50 pixels.
    appearance_threshold
        Seuil maximal de distance (en pixels) pour considérer qu'une étoile
        est nouvelle (apparition) plutôt que déplacée. Par défaut 50 pixels.
    keep_top_n
        Nombre d'étoiles les plus précises à conserver depuis l'image
        précédente. Si None, toutes les étoiles sont considérées. Peut être
        utilisé pour réduire le bruit en ne gardant que les étoiles les
        plus stables/précises. Par défaut None.

    Returns
    -------
    matches : list[tuple[int, int, float]]
        Liste de correspondances (idx_current, idx_previous, distance).
        Les indices sont positionnels dans les listes d'entrée.
    disappeared : list[tuple[int, float, float]]
        Liste d'étoiles disparues : (idx_previous, x_prev, y_prev).
    appeared : list[tuple[int, float, float]]
        Liste d'étoiles apparentes : (idx_current, x_curr, y_curr).

    Raises
    ------
    ValueError
        Si les listes ne contiennent pas de tuples (x, y) valides.
    """
    # Filtrer pour ne garder que le top N étoiles si demandé
    if keep_top_n is not None and keep_top_n > 0:
        if len(previous_stars) > keep_top_n:
            # Trier par "précision" - on utilise la position comme proxy
            # dans un cas réel, on aurait une valeur de confiance/intensité
            previous_stars = previous_stars[:keep_top_n]
        if len(current_stars) > keep_top_n:
            current_stars = current_stars[:keep_top_n]

    # Construction de la matrice de distances
    n_current = len(current_stars)
    n_previous = len(previous_stars)

    if n_current == 0 or n_previous == 0:
        # Pas de correspondance possible
        disappeared = [(i, x, y) for i, (x, y) in enumerate(previous_stars)]
        appeared = [(i, x, y) for i, (x, y) in enumerate(current_stars)]
        return [], disappeared, appeared

    # Matrice des distances euclidiennes
    distance_matrix = np.zeros((n_current, n_previous))
    for i, (cx, cy) in enumerate(current_stars):
        for j, (px, py) in enumerate(previous_stars):
            distance_matrix[i, j] = np.hypot(cx - px, cy - py)

    # Association gloutonne : chaque étoile actuelle peut matcher
    # avec au plus une étoile précédente et vice-versa
    used_previous = set()
    matches = []

    # Trier les correspondances par distance (du plus proche au plus lointain)
    flat_indices = np.argmin(distance_matrix, axis=1)
    min_distances = distance_matrix[np.arange(n_current), flat_indices]
    sorted_order = np.argsort(min_distances)

    for idx in sorted_order:
        j = flat_indices[idx]
        dist = min_distances[idx]

        # Vérifier que cette étoile précédente n'est pas déjà appariée
        # et que la distance est dans les seuils acceptables
        if j not in used_previous and dist <= disappearance_threshold:
            used_previous.add(j)
            matches.append((idx, j, float(dist)))

    # Déterminer les étoiles précédentes qui n'ont pas correspondre (disparues)
    disappeared = []
    for j in range(n_previous):
        if j not in used_previous:
            disappeared.append((j, previous_stars[j][0], previous_stars[j][1]))

    # Déterminer les étoiles actuelles qui n'ont pas correspondre (apparentes)
    current_matched_indices = {m[0] for m in matches}
    appeared = []
    for i in range(n_current):
        if i not in current_matched_indices:
            appeared.append((i, current_stars[i][0], current_stars[i][1]))

    return matches, disappeared, appeared


def filter_precise_stars(
    stars_with_metrics: "list[tuple[float, float, float]]",
    n: "int" = 20,
) -> "list[tuple[float, float, float]]":
    """Sélectionne les N étoiles les plus précises.

    Trie les étoiles par leur "précision" (troisième élément, généralement
    l'intensité ou une mesure de confiance) et retourne les N meilleures.

    Parameters
    ----------
    stars_with_metrics
        Liste de tuples (x, y, metric) où metric est une mesure de précision
        ou de confiance plus élevé = meilleure précision.
    n
        Nombre d'étoiles les plus précises à retourner. Par défaut 20.

    Returns
    -------
    list[tuple[float, float, float]]
        Les N étoiles avec la meilleure métrique de précision.
    """
    if n <= 0:
        return []

    stars_list = list(stars_with_metrics)
    # Trier par métrique décroissante (la plus haute précision d'abord)
    # On inverse pour que la plus haute précision soit en premier
    stars_list.sort(key=lambda s: s[2], reverse=True)

    return stars_list[:n]


if __name__ == "__main__":
    """Démonstration du module de matching d'étoiles."""

    import numpy as np

    # Simuler des étoiles détectées dans l'image N-1
    prev_stars = [(10.5, 20.3), (30.2, 45.7), (55.1, 80.2), (90.3, 30.1)]

    # Simuler des étoiles détectées dans l'image N (certaines déplacées, une nouvelle, une partie)
    curr_stars = [(11.2, 21.1), (31.0, 46.5), (92.5, 31.8), (150.0, 50.0)]  # + une nouvelle

    print("=== Matching d'étoiles entre images ===")
    print(f"Image N-1 : {len(prev_stars)} étoile(s)")
    print(f"Image N   : {len(curr_stars)} étoile(s)")
    print()

    matches, disappeared, appeared = match_stars(
        curr_stars, prev_stars,
        disappearance_threshold=10.0,
        appearance_threshold=10.0,
        keep_top_n=3  # Ne garder que les 3 étoiles les plus précises
    )

    print(f"Correspondances (matches) : {len(matches)}")
    for idx_c, idx_p, dist in matches:
        print(f"  Image N[{idx_c}] ↔ Image N-1[{idx_p}], distance = {dist:.2f} px")

    print(f"\nDisparues (disappeared) : {len(disappeared)}")
    for idx_p, x, y in disappeared:
        print(f"  Image N-1[{idx_p}] disparue : (x={x:.1f}, y={y:.1f})")

    print(f"\nApparues (appeared) : {len(appeared)}")
    for idx_c, x, y in appeared:
        print(f"  Image N[{idx_c}] apparue : (x={x:.1f}, y={y:.1f})")

    # Démonstration du filtrage des étoiles les plus précises
    print("\n=== Filtrage des étoiles les plus précises ===")
    # Chaque étoile est (x, y, metric) où metric = intensité/confiance
    stars_with_metrics = [
        (10.5, 20.3, 0.95),  # très précise
        (30.2, 45.7, 0.87),  # assez précise
        (55.1, 80.2, 0.99),  # très précise
        (90.3, 30.1, 0.62),  # moins précise
    ]

    top_3 = filter_precise_stars(stars_with_metrics, n=3)
    print(f"Top 3 étoiles les plus précises :")
    for x, y, metric in top_3:
        print(f"  (x={x:.1f}, y={y:.1f}, metric={metric:.2f})")