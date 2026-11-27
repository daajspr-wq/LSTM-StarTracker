"""Détection d'étoiles dans une image astronomique."""

import numpy as np
from scipy import ndimage as ndi
from scipy.optimize import curve_fit

def gauss2d(coords, amp, x0, y0, sx, sy, theta, offset):
    """Gaussienne 2D elliptique (sx, sy = sigmas le long des axes tournés de theta)."""
    x, y = coords
    dx, dy = x - x0, y - y0
    xr = dx * np.cos(theta) + dy * np.sin(theta)
    yr = -dx * np.sin(theta) + dy * np.cos(theta)
    return offset + amp * np.exp(-0.5 * ((xr / sx) ** 2 + (yr / sy) ** 2))


def detect_stars(img, nsigma=5.0, smooth_sigma=1.5, bg_box=64,
                 min_separation=6, cutout=7, fwhm_min=1.5, fwhm_max=12.0):
    """Détecte les étoiles et retourne une liste de dictionnaires."""
    # --- Fond et bruit -------------------------------------------------
    bg = ndi.median_filter(ndi.zoom(img, 1 / 4, order=1), size=bg_box // 4)
    bg = ndi.zoom(bg, 4, order=1)[:img.shape[0], :img.shape[1]]
    resid = img - bg
    noise = 1.4826 * np.median(np.abs(resid - np.median(resid)))   # MAD -> sigma

    # --- Lissage et seuillage ------------------------------------------
    smooth = ndi.gaussian_filter(resid, smooth_sigma)
    noise_s = 1.4826 * np.median(np.abs(smooth - np.median(smooth)))
    threshold = nsigma * noise_s

    # --- Maxima locaux --------------------------------------------------
    local_max = (smooth == ndi.maximum_filter(smooth, size=2 * min_separation + 1))
    ys, xs = np.nonzero(local_max & (smooth > threshold))

    k = 2 * np.sqrt(2 * np.log(2))                                  # sigma -> FWHM
    stars = []
    for y, x in zip(ys, xs):
        y0, y1 = max(0, y - cutout), min(img.shape[0], y + cutout + 1)
        x0, x1 = max(0, x - cutout), min(img.shape[1], x + cutout + 1)
        stamp = resid[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]

        p0 = [stamp.max(), x, y, 1.8, 1.8, 0.0, 0.0]
        bounds = ([0, x - 3, y - 3, 0.5, 0.5, -np.pi, -np.inf],
                  [np.inf, x + 3, y + 3, fwhm_max / k, fwhm_max / k, np.pi, np.inf])
        try:
            popt, _ = curve_fit(gauss2d, (xx.ravel(), yy.ravel()), stamp.ravel(),
                                p0=p0, bounds=bounds, maxfev=2000)
        except (RuntimeError, ValueError):
            continue

        amp, xc, yc, sx, sy, theta, off = popt
        fwhm_a, fwhm_b = k * max(sx, sy), k * min(sx, sy)
        if sy > sx:
            theta += np.pi / 2
        fwhm = np.sqrt(fwhm_a * fwhm_b)
        if not (fwhm_min < fwhm < fwhm_max):
            continue

        stars.append(dict(x=xc, y=yc, fwhm_a=fwhm_a, fwhm_b=fwhm_b,
                          theta=np.degrees(theta), fwhm=fwhm,
                          amp=amp, flux=2 * np.pi * amp * sx * sy))
    return stars, bg, noise