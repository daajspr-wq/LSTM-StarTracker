#!/usr/bin/env python3
"""Tests unitaires pour le module de prétraitement d'images.

Couvre :
- La fonction preprocess_image avec la formule (raw - dark) / (flat - bias)
- La fonction load_fits pour charger des images FITS
- Les cas de validation de format
- La gestion des divisions par zéro
"""

import numpy as np
import tempfile
import os

from src.guiding.preprocessing.preprocessing import preprocess_image, load_fits


class TestPreprocessImage:
    """Tests pour la fonction preprocess_image."""

    def test_basic_calibration(self):
        """Test de base : applique (raw - dark) / (flat - bias)."""
        raw = np.array([[100, 200], [300, 400]], dtype=float)
        dark = np.array([[10, 20], [30, 40]], dtype=float)
        flat = np.array([[50, 60], [70, 80]], dtype=float)
        bias = np.array([[5, 5], [5, 5]], dtype=float)

        result = preprocess_image(raw, dark, flat, bias)

        # (100 - 10) / (50 - 5) = 90 / 45 = 2.0
        # (200 - 20) / (60 - 5) = 180 / 55 ≈ 3.27
        # (300 - 30) / (70 - 5) = 270 / 65 ≈ 4.15
        # (400 - 40) / (80 - 5) = 360 / 75 = 4.8
        expected = np.array([[2.0, 180/55], [270/65, 4.8]], dtype=float)
        assert np.allclose(result, expected, rtol=1e-10), f"Résultat {result} ne correspond pas à {expected}"

    def test_shape_validation_dark(self):
        """Le dark doit avoir le même format que raw."""
        raw = np.array([[1, 2], [3, 4]], dtype=float)
        dark_wrong = np.array([[1, 2, 3]], dtype=float)

        try:
            preprocess_image(raw, dark_wrong, np.array([[1, 2], [3, 4]], dtype=float),
                           np.array([[1, 2], [3, 4]], dtype=float))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "dark doit avoir le même format que raw" in str(e)

    def test_shape_validation_flat(self):
        """Le flat doit avoir le même format que raw."""
        raw = np.array([[1, 2], [3, 4]], dtype=float)
        flat_wrong = np.array([[1, 2, 3]], dtype=float)

        try:
            preprocess_image(raw, np.array([[1, 2], [3, 4]], dtype=float), flat_wrong,
                           np.array([[1, 2], [3, 4]], dtype=float))
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "flat doit avoir le même format que raw" in str(e)

    def test_shape_validation_bias(self):
        """Le bias doit avoir le même format que raw."""
        raw = np.array([[1, 2], [3, 4]], dtype=float)
        bias_wrong = np.array([[1, 2, 3]], dtype=float)

        try:
            preprocess_image(raw, np.array([[1, 2], [3, 4]], dtype=float),
                           np.array([[1, 2], [3, 4]], dtype=float), bias_wrong)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "bias doit avoir le même format que raw" in str(e)

    def test_division_by_zero(self):
        """Gestion des divisions par zéro : flat - bias = 0."""
        raw = np.array([[100, 200], [300, 400]], dtype=float)
        dark = np.array([[10, 20], [30, 40]], dtype=float)
        flat = np.array([[5, 5], [5, 5]], dtype=float)  # flat - bias = 0 si bias = flat
        bias = np.array([[5, 5], [5, 5]], dtype=float)

        result = preprocess_image(raw, dark, flat, bias)

        # Avec np.errstate, les divisions par zéro donnent 0.0
        assert result.shape == raw.shape
        assert np.all((result == 0.0) | np.isfinite(result))

    def test_all_zero_denominator(self):
        """Tout le dénominateur est zéro."""
        raw = np.random.rand(5, 5).astype(float) * 100
        dark = np.zeros((5, 5), dtype=float)
        flat = np.ones((5, 5), dtype=float)
        bias = np.ones((5, 5), dtype=float)  # flat - bias = 0

        result = preprocess_image(raw, dark, flat, bias)
        assert np.all(result == 0.0)


class TestLoadFits:
    """Tests pour la fonction load_fits."""

    def test_load_fits_requires_astropy(self):
        """load_fits lève ImportError si astropy n'est pas disponible."""
        # On teste en forçant HAS_ASTROPY à False en important après
        import src.guiding.preprocessing.preprocessing as mod
        original_has = mod.HAS_ASTROPY
        mod.HAS_ASTROPY = False

        try:
            load_fits("/tmp/test.fits")
            assert False, "Should have raised ImportError"
        except ImportError as e:
            assert "astropy est requis" in str(e)
        finally:
            mod.HAS_ASTROPY = original_has

    def test_load_fits_file_not_found(self):
        """load_fits lève FileNotFoundError si le fichier n'existe pas."""
        import src.guiding.preprocessing.preprocessing as mod
        original_has = mod.HAS_ASTROPY
        mod.HAS_ASTROPY = True

        try:
            load_fits("/tmp/nonexistent_file.fits")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError as e:
            assert "Fichier introuvable" in str(e)
        finally:
            mod.HAS_ASTROPY = original_has

    def test_load_fits_actual_file(self):
        """load_fits charge correctement un fichier FITS réel."""
        import astropy.io.fits as fits

        # Création d'un fichier FITS de test
        test_data = np.random.rand(10, 10).astype(float)
        with tempfile.NamedTemporaryFile(suffix=".fits", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            hdu = fits.PrimaryHDU(data=test_data)
            hdu.writeto(tmp_path, overwrite=True)

            loaded = load_fits(tmp_path)
            assert loaded.shape == test_data.shape
            assert np.allclose(loaded, test_data)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    import pytest
    print("Exécution des tests unitaires...")
    print("Tous les tests sont prêts à être exécutés avec pytest.")