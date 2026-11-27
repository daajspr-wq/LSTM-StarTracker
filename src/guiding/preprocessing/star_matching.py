from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
import numpy as np 
from scipy import ndimage as ndi


def estimate_global_shift(pos1, pos2, max_shift=25.0, bin_size=1.0, refine_radius=2.0):
    """Estime le décalage (dx, dy) pos1 -> pos2 par vote dans l'histogramme des différences."""
    diffs = (pos2[None, :, :] - pos1[:, None, :]).reshape(-1, 2)
    diffs = diffs[np.all(np.abs(diffs) < max_shift, axis=1)]
    edges = np.arange(-max_shift, max_shift + bin_size, bin_size)
    hist, xe, ye = np.histogram2d(diffs[:, 0], diffs[:, 1], bins=[edges, edges])
    hist = ndi.gaussian_filter(hist, 1.0)                      # tolère le jitter individuel
    ix, iy = np.unravel_index(np.argmax(hist), hist.shape)
    peak = np.array([(xe[ix] + xe[ix + 1]) / 2, (ye[iy] + ye[iy + 1]) / 2])
    close = np.linalg.norm(diffs - peak, axis=1) < refine_radius
    return np.median(diffs[close], axis=0)


def match_stars(stars_a, stars_b, ref_idx, sigma_pos=1.0, sigma_flux=0.25, gate=3.0):
    """Apparie les étoiles de deux listes de détections.
    Retourne (liste de tuples (i, j, score), décalage global estimé).
    Le premier tuple est la paire de l'étoile de référence (indice ref_idx dans stars_a) ;
    les autres tuples ne sont pas triés."""
    pos_a = np.array([[s["x"], s["y"]] for s in stars_a])
    pos_b = np.array([[s["x"], s["y"]] for s in stars_b])
    flux_a = np.array([s["flux"] for s in stars_a])
    flux_b = np.array([s["flux"] for s in stars_b])

    shift = estimate_global_shift(pos_a, pos_b)

    # Coût = distance résiduelle (après décalage) et différence de log-flux, en "sigmas"
    d_pos = cdist(pos_a + shift, pos_b)
    d_flux = np.abs(np.log(flux_a[:, None] / flux_b[None, :]))
    cost = np.sqrt((d_pos / sigma_pos) ** 2 + (d_flux / sigma_flux) ** 2)

    # Porte : on interdit les paires trop éloignées après correction
    cost_gated = np.where(d_pos > gate * sigma_pos, 1e6, cost)
    rows, cols = linear_sum_assignment(cost_gated)

    matches = [(int(i), int(j), float(cost[i, j])) for i, j in zip(rows, cols)
               if cost_gated[i, j] < 1e6]

    # L'étoile de référence passe en tête de liste, le reste garde son ordre
    k = next((n for n, m in enumerate(matches) if m[0] == ref_idx), None)
    if k is None:
        raise ValueError("L'étoile de référence n'a pas de correspondance dans l'image t+1")
    matches.insert(0, matches.pop(k))
    return matches, shift