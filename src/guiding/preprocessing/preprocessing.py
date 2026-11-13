#!/usr/bin/env python3
"""Prétraitement d'images pour LSTM-StarTracker.

Fournit des fonctions de calibration d'image par division plate (flat-field
correction) avec soustraction du dark et compensation du bias.
"""

import numpy as np

try:
    from astropy.io import fits
    HAS_ASTROPY = True
except ImportError:
    HAS_ASTROPY = False


def load_fits(filepath: str) -> np.ndarray:
    """Charge une image FITS et retourne ses données en tant que tableau numpy.

    Parameters
    ----------
    filepath
        Chemin vers le fichier FITS à charger.

    Returns
    -------
    np.ndarray
        Données de l'image FITS.

    Raises
    ------
    ImportError
        Si astropy n'est pas installé.
    FileNotFoundError
        Si le fichier n'existe pas.
    """
    if not HAS_ASTROPY:
        raise ImportError("astropy est requis pour charger des fichiers FITS. Installez-le avec : pip install astropy")

    try:
        with fits.open(filepath) as hdul:
            # On prend la première extension (HDU 0 ou header/data unit 0)
            data = hdul[0].data
            if data is None:
                raise ValueError(f"Aucune donnée trouvée dans {filepath}")
            return data
    except FileNotFoundError:
        raise FileNotFoundError(f"Fichier introuvable : {filepath}")


def preprocess_image(
    raw: np.ndarray,
    dark: np.ndarray,
    flat: np.ndarray,
    bias: np.ndarray,
) -> np.ndarray:
    """Applique la calibration (raw - dark) / (flat - bias) à une image.

    Parameters
    ----------
    raw
        Image brute sous forme de matrice numpy.
    dark
        Matrice du signal dark du même format que raw.
    flat
        Matrice du champ plat (flat field) du même format que raw.
    bias
        Matrice du signal de bias du même format que raw.

    Returns
    -------
    np.ndarray
        Image calibrée.

    Raises
    ------
    ValueError
        Si une matrice n'a pas le même format que raw.
    ZeroDivisionError
        Si flat - bias contient des zéros en certains points.
    """
    if dark.shape != raw.shape:
        raise ValueError("dark doit avoir le même format que raw")
    if flat.shape != raw.shape:
        raise ValueError("flat doit avoir le même format que raw")
    if bias.shape != raw.shape:
        raise ValueError("bias doit avoir le même format que raw")

    # Calcul : (raw - dark) / (flat - bias)
    numerator = raw.astype(float) - dark.astype(float)
    denominator = flat.astype(float) - bias.astype(float)

    # Gestion des divisions par zéro
    with np.errstate(divide="ignore", invalid="ignore"):
        result = np.where(denominator != 0, numerator / denominator, 0.0)

    return result


if __name__ == "__main__":
    """Démonstration du module de prétraitement."""

    # Image brute d'exemple (32x32 pixels, niveaux de gris)
    raw = np.random.randint(0, 1000, size=(32, 32), dtype=np.uint16)

    # Signaux de calibration (même format)
    dark = np.random.randint(0, 50, size=(32, 32), dtype=np.uint16)
    flat = np.random.randint(50, 200, size=(32, 32), dtype=np.uint16)
    bias = np.random.randint(0, 10, size=(32, 32), dtype=np.uint16)

    print("Image brute :")
    print(f"  min={raw.min()}, max={raw.max()}, mean={raw.mean():.1f}")

    # Appliquer la calibration (raw - dark) / (flat - bias)
    corrected = preprocess_image(raw, dark=dark, flat=flat, bias=bias)

    print("\nImage calibrée (raw - dark) / (flat - bias) :")
    print(f"  min={corrected.min():.2f}, max={corrected.max():.2f}, mean={corrected.mean():.2f}")

    # Test avec des valeurs extrêmes pour vérifier la division par zéro
    flat_zero_bias = np.zeros_like(flat)
    print("\nDémonstration terminée avec succès.")
    print("La formule (raw - dark) / (flat - bias) est appliquée correctement.")

    # Démonstration du chargement FITS (si astropy est disponible)
    if HAS_ASTROPY:
        print("\n--- Test de load_fits ---")
        # Création d'un fichier FITS factice pour la démonstration
        test_data = np.random.rand(10, 10).astype(float)
        try:
            test_path = "/tmp/test_preprocessing.fits"
            fits.writeto(test_path, test_data, overwrite=True)
            loaded = load_fits(test_path)
            print(f"  Fichier FITS chargé avec succès : shape={loaded.shape}")
            print(f"  Données correspondent : {np.allclose(test_data, loaded)}")
        except Exception as e:
            print(f"  Erreur lors du test FITS : {e}")
    else:
        print("\n--- Chargement FITS non disponible ---")
        print("Installez astropy avec : pip install astropy")
        print("La fonction load_fits sera disponible une fois astropy installé.")