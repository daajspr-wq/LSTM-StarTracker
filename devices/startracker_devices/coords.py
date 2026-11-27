"""Conversion J2000 <-> JNow.

Les catalogues donnent des coordonnées J2000, alors que la plupart des montures
travaillent en coordonnées « du jour » (JNow, ce qu'ASCOM appelle topocentriques).
La différence dépasse 0,3° aujourd'hui : il faut convertir avant chaque pointage.

J2000 est assimilé à ICRS (écart de quelques millisecondes d'arc), et JNow au repère
apparent géocentrique TETE d'astropy (précession, nutation, aberration). Ce calcul
fonctionne hors ligne.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import astropy.units as u
from astropy.coordinates import ICRS, TETE, SkyCoord
from astropy.time import Time

from .types import Coordinates, Epoch


def convert(c: Coordinates, to: Epoch, when: Optional[dt.datetime] = None) -> Coordinates:
    """Renvoie `c` exprimé dans le repère `to` (à l'instant `when`, défaut : maintenant)."""
    if c.epoch == to:
        return c
    t = Time(when or dt.datetime.now(dt.timezone.utc))
    if c.epoch == Epoch.J2000 and to == Epoch.JNOW:
        out = SkyCoord(ra=c.ra_hours * u.hourangle, dec=c.dec_degrees * u.deg, frame=ICRS()) \
            .transform_to(TETE(obstime=t))
    elif c.epoch == Epoch.JNOW and to == Epoch.J2000:
        out = SkyCoord(ra=c.ra_hours * u.hourangle, dec=c.dec_degrees * u.deg, frame=TETE(obstime=t)) \
            .transform_to(ICRS())
    else:
        raise ValueError(f"conversion {c.epoch} -> {to} non gérée")
    return Coordinates(ra_hours=float(out.ra.hour) % 24.0, dec_degrees=float(out.dec.deg), epoch=to)
