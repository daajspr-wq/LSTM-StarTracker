from star_detection import detect_stars
import numpy as np

def find_reference_star(img, central_fraction=0.5, **detect_kwargs):
    """Détecte les étoiles de img et retourne (indice, étoile de référence, liste des étoiles).
    Zone centrale = rectangle centré, de même rapport d'aspect que l'image, couvrant
    `central_fraction` de la surface (côtés réduits d'un facteur sqrt(fraction))."""
    all_stars, _, _ = detect_stars(img, **detect_kwargs)
    ny, nx = img.shape
    f = np.sqrt(central_fraction)
    half_w, half_h = f * nx / 2, f * ny / 2
    cx, cy = nx / 2, ny / 2

    candidates = [i for i, s in enumerate(all_stars)
                  if abs(s["x"] - cx) <= half_w and abs(s["y"] - cy) <= half_h]
    if not candidates:
        raise ValueError("Aucune étoile dans la zone centrale")

    ref_idx = min(candidates, key=lambda i: all_stars[i]["fwhm"])    # meilleure FWHM = la plus petite
    return ref_idx, all_stars[ref_idx], all_stars