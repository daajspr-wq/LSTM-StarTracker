"""Accès aux montures et caméras via ASCOM Alpaca (bibliothèque alpyca).

Chaîne complète sous Windows :
    application Python -> HTTP (Alpaca) -> ASCOM Remote ou OmniSim -> driver ASCOM -> appareil
Les appareils nativement Alpaca (réseau) sont pilotés directement.

    devs = Devices(servers=["127.0.0.1:32323"])     # OmniSim
    mount = devs.mount(devs.list_devices(DeviceKind.MOUNT)[0].id)
    mount.connect()
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, TypeVar

import numpy as np
import requests
from alpaca import discovery
from alpaca import exceptions as ax
from alpaca.camera import Camera as _AlpacaCamera
from alpaca.camera import CameraStates
from alpaca.telescope import (DriveRates, EquatorialCoordinateType, GuideDirections,
                              PierSide as _AlpacaPierSide, Telescope as _AlpacaTelescope,
                              TelescopeAxes)
from astropy.io import fits

from . import coords
from .errors import (DeviceInternalError, DeviceNotFound, DeviceNotReady, DeviceTimeout,
                     DeviceUnavailable, InvalidParameter, NotSupported, OperationAborted,
                     DeviceError, translate)
from .types import (Axis, CameraInfo, CameraStatus, Coordinates, DeviceInfo, DeviceKind, Epoch,
                    ExposureResult, GuideDirection, MountInfo, MountState, PierSide, TrackingMode)

T = TypeVar("T")
POLL_S = 0.2

_DRIVE = {TrackingMode.SIDEREAL: DriveRates.driveSidereal, TrackingMode.LUNAR: DriveRates.driveLunar,
          TrackingMode.SOLAR: DriveRates.driveSolar, TrackingMode.KING: DriveRates.driveKing}
_DRIVE_BACK = {int(v): k for k, v in _DRIVE.items()}
_GUIDE = {GuideDirection.NORTH: GuideDirections.guideNorth, GuideDirection.SOUTH: GuideDirections.guideSouth,
          GuideDirection.EAST: GuideDirections.guideEast, GuideDirection.WEST: GuideDirections.guideWest}
_AXIS = {Axis.PRIMARY: TelescopeAxes.axisPrimary, Axis.SECONDARY: TelescopeAxes.axisSecondary}
_PIER = {int(_AlpacaPierSide.pierEast): PierSide.EAST, int(_AlpacaPierSide.pierWest): PierSide.WEST}
_CAM_STATE = {int(CameraStates.cameraIdle): "idle", int(CameraStates.cameraWaiting): "waiting",
              int(CameraStates.cameraExposing): "exposing", int(CameraStates.cameraReading): "reading",
              int(CameraStates.cameraDownload): "download", int(CameraStates.cameraError): "error"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _opt(fn: Callable[[], T], default: T = None) -> T:
    """Lit une propriété facultative : renvoie `default` si l'appareil ne la gère pas."""
    try:
        with translate():
            return fn()
    except (NotSupported, DeviceNotReady, InvalidParameter, DeviceInternalError):
        return default


def _check_coords(ra_hours: float, dec_degrees: float) -> None:
    if not (0.0 <= ra_hours < 24.0) or not (-90.0 <= dec_degrees <= 90.0):
        raise InvalidParameter("coordonnées hors limites (AD dans [0, 24) h, Dec dans [-90, 90]°)")


def _wait_until(cond: Callable[[], bool], timeout_s: float, what: str,
                abort: Optional[threading.Event] = None) -> None:
    deadline = time.monotonic() + timeout_s
    while not cond():
        if abort is not None and abort.is_set():
            raise OperationAborted(f"{what} interrompu")
        if time.monotonic() > deadline:
            raise DeviceTimeout(f"{what} : pas terminé après {timeout_s:.0f} s")
        time.sleep(POLL_S)


# ───────────────────────── Base commune ─────────────────────────

class _Device:
    def __init__(self, info: DeviceInfo, dev: Any) -> None:
        self.info_ref = info
        self.id = info.id
        self._dev = dev

    @property
    def connected(self) -> bool:
        with translate():
            return bool(self._dev.Connected)

    def connect(self, timeout_s: float = 30.0) -> None:
        with translate():
            self._dev.Connect()
            _wait_until(lambda: not self._dev.Connecting, timeout_s, "connexion")
            if not self._dev.Connected:
                raise DeviceUnavailable(f"connexion à {self.info_ref.name} impossible")

    def disconnect(self, timeout_s: float = 30.0) -> None:
        with translate():
            self._dev.Disconnect()
            _wait_until(lambda: not self._dev.Connecting, timeout_s, "déconnexion")

    def _close(self) -> None:
        try:
            self._dev.rqs.close()
        except Exception:
            pass


# ───────────────────────── Monture ─────────────────────────

class Mount(_Device):
    """Une monture. Toutes les méthodes lèvent les exceptions de `startracker_devices.errors`."""

    def __init__(self, info: DeviceInfo) -> None:
        server, number = info.server, info.number
        super().__init__(info, _AlpacaTelescope(server, number))
        self._t: _AlpacaTelescope = self._dev
        self._abort_slew = threading.Event()
        self._native: Optional[Epoch] = None

    # -- lecture --
    def native_epoch(self) -> Epoch:
        """Repère de coordonnées de la monture (lu une fois puis gardé en cache)."""
        if self._native is None:
            sys_ = _opt(lambda: int(self._t.EquatorialSystem), int(EquatorialCoordinateType.equTopocentric))
            self._native = Epoch.J2000 if sys_ == int(EquatorialCoordinateType.equJ2000) else Epoch.JNOW
        return self._native

    def info(self) -> MountInfo:
        t = self._t
        with translate():
            t.Connected  # lève DeviceNotReady si non connecté
        can_ra = _opt(lambda: t.CanSetRightAscensionRate, False)
        can_dec = _opt(lambda: t.CanSetDeclinationRate, False)
        gra = _opt(lambda: t.GuideRateRightAscension)
        gdec = _opt(lambda: t.GuideRateDeclination)
        return MountInfo(
            device=self.info_ref,
            can_slew=_opt(lambda: t.CanSlewAsync, False),
            can_sync=_opt(lambda: t.CanSync, False),
            can_park=_opt(lambda: t.CanPark, False),
            can_unpark=_opt(lambda: t.CanUnpark, False),
            can_pulse_guide=_opt(lambda: t.CanPulseGuide, False),
            can_set_tracking=_opt(lambda: t.CanSetTracking, False),
            can_set_custom_rate=bool(can_ra or can_dec),
            can_move_axis=bool(_opt(lambda: t.CanMoveAxis(TelescopeAxes.axisPrimary), False)
                               and _opt(lambda: t.CanMoveAxis(TelescopeAxes.axisSecondary), False)),
            guide_rate_ra_arcsec_per_s=None if gra is None else gra * 3600.0,
            guide_rate_dec_arcsec_per_s=None if gdec is None else gdec * 3600.0,
            site_latitude_deg=_opt(lambda: t.SiteLatitude),
            site_longitude_deg=_opt(lambda: t.SiteLongitude),
            native_epoch=self.native_epoch(),
        )

    def state(self, epoch: Optional[Epoch] = None) -> MountState:
        """État courant. `epoch` : repère voulu pour les coordonnées (défaut : celui de la monture)."""
        t = self._t
        with translate():
            ra, dec = float(t.RightAscension), float(t.Declination)
            tracking = bool(t.Tracking)
            parked = bool(t.AtPark)
            slewing = bool(t.Slewing)
        c = Coordinates(ra % 24.0, dec, self.native_epoch())
        if epoch is not None and epoch != c.epoch:
            c = coords.convert(c, epoch)
        if not tracking:
            mode = TrackingMode.STOPPED
        else:
            mode = _DRIVE_BACK.get(int(_opt(lambda: t.TrackingRate, DriveRates.driveSidereal)), TrackingMode.SIDEREAL)
            if mode == TrackingMode.SIDEREAL and (_opt(lambda: t.RightAscensionRate, 0.0) or
                                                   _opt(lambda: t.DeclinationRate, 0.0)):
                mode = TrackingMode.CUSTOM
        return MountState(
            device_id=self.id, coordinates=c,
            altitude_deg=_opt(lambda: float(t.Altitude), float("nan")),
            azimuth_deg=_opt(lambda: float(t.Azimuth), float("nan")),
            sidereal_time_hours=_opt(lambda: float(t.SiderealTime), float("nan")),
            side_of_pier=_PIER.get(int(_opt(lambda: t.SideOfPier, -1)), PierSide.UNKNOWN),
            slewing=slewing, tracking=tracking, tracking_mode=mode, parked=parked,
            pulse_guiding=bool(_opt(lambda: t.IsPulseGuiding, False)),
            timestamp=_now(),
        )

    def stream_state(self, interval_s: float = 0.5, epoch: Optional[Epoch] = None) -> Iterator[MountState]:
        """Itérateur infini d'états (interrogation périodique). Sortir avec `break`."""
        while True:
            t0 = time.monotonic()
            yield self.state(epoch)
            time.sleep(max(0.0, interval_s - (time.monotonic() - t0)))

    # -- mouvements --
    def slew_to(self, ra_hours: float, dec_degrees: float, epoch: Epoch,
                timeout_s: float = 300.0) -> MountState:
        """Pointe et bloque jusqu'à l'arrivée. `epoch` est obligatoire : un catalogue donne du J2000.

        Lève OperationAborted si stop_slew() est appelé pendant le pointage.
        """
        _check_coords(ra_hours, dec_degrees)
        t = self._t
        with translate():
            if not _opt(lambda: t.CanSlewAsync, False):
                raise NotSupported("cette monture ne sait pas pointer des coordonnées (SlewToCoordinatesAsync)")
            if t.AtPark:
                raise DeviceNotReady("monture parquée : appeler unpark() d'abord")
            if not t.Tracking:
                raise DeviceNotReady("le suivi est arrêté : appeler set_tracking() avant un pointage")
            if t.Slewing:
                raise DeviceNotReady("un pointage est déjà en cours")
            target = coords.convert(Coordinates(ra_hours, dec_degrees, epoch), self.native_epoch())
            self._abort_slew.clear()
            t.SlewToCoordinatesAsync(target.ra_hours, target.dec_degrees)
            time.sleep(POLL_S)
            _wait_until(lambda: not t.Slewing, timeout_s, "pointage", self._abort_slew)
            if self._abort_slew.is_set():
                raise OperationAborted("pointage interrompu")
        return self.state()

    def stop_slew(self) -> None:
        """Arrête un pointage ou un mouvement d'axe en cours (sans arrêter le suivi)."""
        self._abort_slew.set()
        with translate():
            self._t.AbortSlew()

    def sync(self, ra_hours: float, dec_degrees: float, epoch: Epoch) -> None:
        """Indique à la monture qu'elle vise ces coordonnées (après un plate solving, par ex.)."""
        _check_coords(ra_hours, dec_degrees)
        t = self._t
        with translate():
            if not _opt(lambda: t.CanSync, False):
                raise NotSupported("synchronisation non supportée par cette monture")
            c = coords.convert(Coordinates(ra_hours, dec_degrees, epoch), self.native_epoch())
            t.SyncToCoordinates(c.ra_hours, c.dec_degrees)

    def set_tracking(self, mode: TrackingMode, ra_rate: float = 0.0, dec_rate: float = 0.0) -> MountState:
        """Règle le suivi.

        Pour TrackingMode.CUSTOM, unités ASCOM (décalages par rapport au sidéral) :
          ra_rate  : secondes d'AD par seconde sidérale
          dec_rate : secondes d'arc par seconde
        """
        t = self._t
        with translate():
            if mode == TrackingMode.STOPPED:
                if not _opt(lambda: t.CanSetTracking, False):
                    raise NotSupported("le suivi ne peut pas être arrêté sur cette monture")
                t.Tracking = False
                return self.state()
            if t.AtPark:
                raise DeviceNotReady("monture parquée : appeler unpark() d'abord")
            drive = _DRIVE[TrackingMode.SIDEREAL if mode == TrackingMode.CUSTOM else mode]
            supported = {int(r) for r in (_opt(lambda: t.TrackingRates, None) or [DriveRates.driveSidereal])}
            if int(drive) not in supported:
                raise NotSupported(f"vitesse de suivi {mode.value} non supportée par cette monture")
            if _opt(lambda: t.CanSetTracking, False):
                t.Tracking = True
            elif not t.Tracking:
                raise NotSupported("le suivi ne peut pas être activé à distance sur cette monture")
            if int(_opt(lambda: t.TrackingRate, -1)) != int(drive):
                t.TrackingRate = drive
            want_ra, want_dec = (ra_rate, dec_rate) if mode == TrackingMode.CUSTOM else (0.0, 0.0)
            for value, can, prop in ((want_ra, "CanSetRightAscensionRate", "RightAscensionRate"),
                                     (want_dec, "CanSetDeclinationRate", "DeclinationRate")):
                if _opt(lambda: getattr(t, can), False):
                    setattr(t, prop, value)
                elif value != 0.0:
                    raise NotSupported(f"{prop} non réglable sur cette monture")
        return self.state()

    def pulse_guide(self, direction: GuideDirection, duration_ms: int, wait: bool = True) -> None:
        """Impulsion de guidage. Avec wait=True, rend la main quand l'impulsion est finie."""
        if not (0 < duration_ms <= 10_000):
            raise InvalidParameter("durée d'impulsion invalide (1 à 10 000 ms)")
        t = self._t
        with translate():
            if not _opt(lambda: t.CanPulseGuide, False):
                raise NotSupported("guidage par impulsions non supporté par cette monture")
            if t.AtPark:
                raise DeviceNotReady("monture parquée")
            t.PulseGuide(_GUIDE[direction], int(duration_ms))
            if wait:
                _wait_until(lambda: not t.IsPulseGuiding, duration_ms / 1000.0 + 5.0, "impulsion de guidage")

    def move_axis(self, axis: Axis, rate_deg_per_s: float) -> None:
        """Fait tourner un axe à vitesse constante. 0 = arrêt de l'axe."""
        t = self._t
        a = _AXIS[axis]
        with translate():
            if not _opt(lambda: t.CanMoveAxis(a), False):
                raise NotSupported(f"mouvement de l'axe {axis.value} non supporté")
            if t.AtPark:
                raise DeviceNotReady("monture parquée")
            if rate_deg_per_s != 0.0:
                rates = _opt(lambda: t.AxisRates(a), []) or []
                if rates and not any(r.Minimum <= abs(rate_deg_per_s) <= r.Maximum for r in rates):
                    ranges = ", ".join(f"[{r.Minimum}, {r.Maximum}]" for r in rates)
                    raise InvalidParameter(f"vitesse hors des plages acceptées : {ranges} °/s")
            t.MoveAxis(a, rate_deg_per_s)

    def park(self, timeout_s: float = 300.0) -> MountState:
        t = self._t
        with translate():
            if not _opt(lambda: t.CanPark, False):
                raise NotSupported("parcage non supporté par cette monture")
            t.Park()
            _wait_until(lambda: t.AtPark and not t.Slewing, timeout_s, "parcage")
        return self.state()

    def unpark(self) -> MountState:
        t = self._t
        with translate():
            if t.AtPark:
                if not _opt(lambda: t.CanUnpark, False):
                    raise NotSupported("cette monture ne peut pas être déparquée à distance")
                t.Unpark()
                _wait_until(lambda: not t.AtPark, 60.0, "déparcage")
        return self.state()


# ───────────────────────── Caméra ─────────────────────────

class Camera(_Device):
    def __init__(self, info: DeviceInfo) -> None:
        super().__init__(info, _AlpacaCamera(info.server, info.number))
        self._c: _AlpacaCamera = self._dev
        self._abort = threading.Event()

    def info(self) -> CameraInfo:
        c = self._c
        with translate():
            width, height = int(c.CameraXSize), int(c.CameraYSize)
            px, py = float(c.PixelSizeX), float(c.PixelSizeY)
        gmin = _opt(lambda: int(c.GainMin))
        return CameraInfo(
            device=self.info_ref, sensor_name=_opt(lambda: c.SensorName, "") or "",
            width_px=width, height_px=height, pixel_size_um_x=px, pixel_size_um_y=py,
            max_adu=int(_opt(lambda: c.MaxADU, 65535)),
            gain_min=gmin, gain_max=_opt(lambda: int(c.GainMax)),
            exposure_min_s=float(_opt(lambda: c.ExposureMin, 0.0)),
            exposure_max_s=float(_opt(lambda: c.ExposureMax, 3600.0)),
            max_bin_x=int(_opt(lambda: c.MaxBinX, 1)), max_bin_y=int(_opt(lambda: c.MaxBinY, 1)),
            can_set_gain=gmin is not None, can_cool=bool(_opt(lambda: c.CanSetCCDTemperature, False)),
            can_abort=bool(_opt(lambda: c.CanAbortExposure, False)),
        )

    def status(self) -> CameraStatus:
        c = self._c
        with translate():
            state = _CAM_STATE.get(int(c.CameraState), "error")
            bx, by = int(c.BinX), int(c.BinY)
        can_cool = bool(_opt(lambda: c.CanSetCCDTemperature, False))
        return CameraStatus(
            device_id=self.id, state=state,
            temperature_c=_opt(lambda: float(c.CCDTemperature)),
            cooler_on=_opt(lambda: bool(c.CoolerOn)) if can_cool else None,
            cooler_power_percent=_opt(lambda: float(c.CoolerPower))
            if can_cool and _opt(lambda: c.CanGetCoolerPower, False) else None,
            gain=_opt(lambda: int(c.Gain)), bin_x=bx, bin_y=by,
        )

    def configure(self, *, gain: Optional[int] = None, offset: Optional[int] = None,
                  bin_x: Optional[int] = None, bin_y: Optional[int] = None,
                  target_temperature_c: Optional[float] = None,
                  cooler_on: Optional[bool] = None) -> CameraStatus:
        """Modifie uniquement les réglages fournis. Changer le binning repasse en plein champ."""
        c = self._c
        with translate():
            if _CAM_STATE.get(int(c.CameraState)) in ("exposing", "reading", "download"):
                raise DeviceNotReady("pose en cours")
            if gain is not None:
                c.Gain = int(gain)
            if offset is not None:
                c.Offset = int(offset)
            if bin_x is not None or bin_y is not None:
                bx = int(bin_x if bin_x is not None else bin_y)
                by = int(bin_y if bin_y is not None else bx)
                c.BinX, c.BinY = bx, by
                c.StartX, c.StartY = 0, 0
                c.NumX, c.NumY = int(c.CameraXSize) // bx, int(c.CameraYSize) // by
            if target_temperature_c is not None:
                c.SetCCDTemperature = float(target_temperature_c)
            if cooler_on is not None:
                c.CoolerOn = bool(cooler_on)
        return self.status()

    def expose(self, exposure_s: float, output_dir: str | Path, filename_stem: str = "", *,
               light: bool = True, on_progress: Optional[Callable[[float, float], None]] = None,
               extra_header: Optional[dict] = None, timeout_s: float = 120.0) -> ExposureResult:
        """Prend une pose, écrit un FITS et renvoie ses métadonnées (bloquant).

        on_progress(écoulé_s, total_s) est appelé pendant la pose.
        timeout_s : délai accordé après la fin théorique de la pose (lecture + téléchargement).
        Lève OperationAborted si abort() est appelé.
        """
        out = Path(output_dir)
        if not out.is_dir():
            raise InvalidParameter(f"dossier de sortie inexistant : {out}")
        c = self._c
        with translate():
            emin = float(_opt(lambda: c.ExposureMin, 0.0))
            emax = float(_opt(lambda: c.ExposureMax, 3600.0))
            if not (emin <= exposure_s <= emax) or exposure_s <= 0:
                raise InvalidParameter(f"durée de pose hors limites [{emin}, {emax}] s")
            if _CAM_STATE.get(int(c.CameraState)) in ("exposing", "reading", "download"):
                raise DeviceNotReady("une pose est déjà en cours")

            self._abort.clear()
            start = _now()
            c.StartExposure(float(exposure_s), bool(light))
            t0 = time.monotonic()
            deadline = t0 + exposure_s + timeout_s
            while True:
                elapsed = time.monotonic() - t0
                if self._abort.is_set():
                    raise OperationAborted("pose interrompue")
                if c.ImageReady:
                    break
                state = _CAM_STATE.get(int(c.CameraState), "error")
                if state == "error":
                    raise DeviceInternalError("la caméra signale une erreur pendant la pose")
                if state == "idle" and elapsed > exposure_s + 5.0:
                    raise OperationAborted("pose interrompue côté appareil")
                if time.monotonic() > deadline:
                    raise DeviceTimeout("image non disponible dans le délai imparti")
                if on_progress is not None:
                    on_progress(min(elapsed, exposure_s), exposure_s)
                time.sleep(min(POLL_S, max(0.01, exposure_s - elapsed)) if elapsed < exposure_s else POLL_S)

            data = self._download()
            temp = _opt(lambda: float(c.CCDTemperature))
            real_start = _parse_alpaca_time(_opt(lambda: c.LastExposureStartTime)) or start
            hdr = fits.Header()
            hdr["EXPTIME"] = (float(_opt(lambda: c.LastExposureDuration, exposure_s)), "[s] exposure time")
            hdr["DATE-OBS"] = (real_start.strftime("%Y-%m-%dT%H:%M:%S.%f"), "UTC start of exposure")
            hdr["IMAGETYP"] = "Light Frame" if light else "Dark Frame"
            hdr["INSTRUME"] = self.info_ref.name
            hdr["XBINNING"], hdr["YBINNING"] = int(c.BinX), int(c.BinY)
            for key, getter in (("GAIN", lambda: int(c.Gain)), ("OFFSET", lambda: int(c.Offset)),
                                ("XPIXSZ", lambda: float(c.PixelSizeX) * int(c.BinX)),
                                ("YPIXSZ", lambda: float(c.PixelSizeY) * int(c.BinY))):
                v = _opt(getter)
                if v is not None:
                    hdr[key] = v
            if temp is not None:
                hdr["CCD-TEMP"] = (round(temp, 2), "[C] sensor temperature")
        for k, v in (extra_header or {}).items():
            hdr[k] = v

        stem = filename_stem or "img_" + start.strftime("%Y%m%dT%H%M%S_%f")
        path = (out / f"{stem}.fits").resolve()
        if path.exists():
            raise InvalidParameter(f"le fichier existe déjà : {path}")
        fits.PrimaryHDU(data=data, header=hdr).writeto(path)
        height, width = data.shape[-2], data.shape[-1]
        return ExposureResult(fits_path=path, width_px=width, height_px=height,
                              start_time=real_start, exposure_s=exposure_s, temperature_c=temp)

    def abort(self) -> None:
        """Interrompt la pose en cours (l'image est perdue)."""
        self._abort.set()
        c = self._c
        with translate():
            if _opt(lambda: c.CanAbortExposure, False):
                c.AbortExposure()
            elif _opt(lambda: c.CanStopExposure, False):
                c.StopExposure()

    def _download(self) -> np.ndarray:
        """Récupère l'image (format binaire ImageBytes si possible, sinon JSON) en (y, x)."""
        c = self._c
        try:
            raw = c.ImageArrayRaw                       # rapide : application/imagebytes
            meta = c.ImageArrayInfo
            arr = np.frombuffer(raw, dtype=np.dtype(raw.typecode))
            dims = (meta.Dimension1, meta.Dimension2) + ((meta.Dimension3,) if meta.Rank == 3 else ())
            arr = arr.reshape(dims)
        except ax.InvalidValueException:                 # le serveur a répondu en JSON
            arr = np.asarray(c.ImageArray)
        # Alpaca transmet [x][y](...[couleur]) ; FITS attend (y, x), ou (couleur, y, x).
        arr = arr.T if arr.ndim == 2 else np.transpose(arr, (2, 1, 0))
        max_adu = int(_opt(lambda: c.MaxADU, 65535))
        if np.issubdtype(arr.dtype, np.integer) and max_adu <= 65535 and arr.min(initial=0) >= 0:
            return arr.astype(np.uint16)
        return np.ascontiguousarray(arr)


def _parse_alpaca_time(s: Optional[str]) -> Optional[dt.datetime]:
    """LastExposureStartTime est au format FITS « AAAA-MM-JJThh:mm:ss[.fff] », en UTC."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ───────────────────────── Point d'entrée ─────────────────────────

DEFAULT_LOCAL_SERVERS = ("127.0.0.1:32323",   # OmniSim (simulateurs ASCOM)
                         "127.0.0.1:11111")   # ASCOM Remote (drivers ASCOM du PC)


class Devices:
    """Annuaire des appareils accessibles.

    servers  : adresses « hôte:port » de serveurs Alpaca. Par défaut (None) : OmniSim et
               ASCOM Remote sur ce PC (DEFAULT_LOCAL_SERVERS). La variable d'environnement
               ALPACA_SERVERS (adresses séparées par des virgules) s'y ajoute toujours.
    discover : cherche aussi les serveurs du réseau local (diffusion UDP Alpaca). Désactivé
               par défaut : compter environ 1,5 s par carte réseau du PC.
    """

    def __init__(self, servers: Optional[Iterable[str]] = None, discover: bool = False,
                 discovery_timeout_s: int = 1) -> None:
        self._defaults = set(DEFAULT_LOCAL_SERVERS) if servers is None else set()
        self._servers = list(DEFAULT_LOCAL_SERVERS) if servers is None else list(servers)
        env = os.environ.get("ALPACA_SERVERS", "")
        extra = [s.strip() for s in env.split(",") if s.strip()]
        self._servers += extra
        self._defaults -= set(extra)
        self._discover, self._disc_timeout = discover, discovery_timeout_s
        self._known: dict[str, DeviceInfo] = {}
        self._objects: dict[str, _Device] = {}
        self.last_errors: dict[str, str] = {}       # serveur -> erreur, pour un écran de diagnostic
        self._lock = threading.Lock()

    def __enter__(self) -> "Devices":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for d in self._objects.values():
            d._close()
        self._objects.clear()

    def servers(self, rescan: bool = False) -> list[str]:
        found = list(dict.fromkeys(self._servers))
        if self._discover and (rescan or not self._known):
            try:
                found += discovery.search_ipv4(numquery=1, timeout=self._disc_timeout)
            except Exception as e:                     # réseau sans diffusion, pare-feu...
                self.last_errors["discovery"] = str(e)
        return [s.replace("localhost", "127.0.0.1") for s in dict.fromkeys(found)]

    def list_devices(self, kind: Optional[DeviceKind] = None, rescan: bool = False) -> list[DeviceInfo]:
        """Liste montures et caméras. Les serveurs injoignables sont ignorés (voir last_errors)."""
        with self._lock:
            if rescan or not self._known:
                self.last_errors.clear()
                known: dict[str, DeviceInfo] = {}
                for server in self.servers(rescan):
                    try:
                        with translate():
                            for d in _configured_devices(server):
                                dtype = str(d.get("DeviceType", "")).lower()
                                if dtype not in (DeviceKind.MOUNT.value, DeviceKind.CAMERA.value):
                                    continue
                                n = int(d["DeviceNumber"])
                                info = DeviceInfo(id=f"{server}/{dtype}/{n}", kind=DeviceKind(dtype),
                                                  name=str(d.get("DeviceName", "")), server=server,
                                                  number=n, unique_id=str(d.get("UniqueID", "")))
                                known[info.id] = info
                    except DeviceUnavailable as e:
                        if server not in self._defaults:      # absent par défaut = normal
                            self.last_errors[server] = str(e)
                    except (DeviceError, ValueError, KeyError) as e:
                        self.last_errors[server] = str(e) or type(e).__name__
                self._known = known
            return [d for d in self._known.values() if kind is None or d.kind == kind]

    def mount(self, device_id: str) -> Mount:
        return self._get(device_id, DeviceKind.MOUNT, Mount)

    def camera(self, device_id: str) -> Camera:
        return self._get(device_id, DeviceKind.CAMERA, Camera)

    def _get(self, device_id: str, kind: DeviceKind, cls: type) -> Any:
        if device_id in self._objects:
            obj = self._objects[device_id]
            if not isinstance(obj, cls):
                raise DeviceNotFound(f"{device_id} n'est pas un appareil de type {kind.value}")
            return obj
        info = self._known.get(device_id) or _parse_id(device_id)
        if info.kind != kind:
            raise DeviceNotFound(f"{device_id} n'est pas un appareil de type {kind.value}")
        obj = cls(info)
        self._objects[device_id] = obj
        return obj


def _configured_devices(server: str, timeout_s: float = 3.0) -> list[dict]:
    """GET /management/v1/configureddevices, avec un délai (celui d'alpyca n'en a pas)."""
    r = requests.get(f"http://{server}/management/v1/configureddevices", timeout=timeout_s)
    if r.status_code != 200:
        raise ax.AlpacaRequestException(r.status_code, r.text[:200])
    j = r.json()
    if j.get("ErrorNumber"):
        raise ax.DriverException(int(j["ErrorNumber"]), str(j.get("ErrorMessage", "")))
    return list(j.get("Value") or [])


def _parse_id(device_id: str) -> DeviceInfo:
    """Accepte un identifiant écrit à la main : « hôte:port/telescope/0 »."""
    try:
        server, dtype, num = device_id.rsplit("/", 2)
        kind = DeviceKind(dtype.lower())
        n = int(num)
    except ValueError as e:
        raise DeviceNotFound(f"identifiant invalide {device_id!r} (attendu « hôte:port/type/numéro »)") from e
    return DeviceInfo(id=device_id, kind=kind, name=f"{kind.value} {n} @ {server}", server=server, number=n)
