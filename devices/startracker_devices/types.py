"""Types publics renvoyés par la bibliothèque.

Ce sont de simples dataclasses : le reste de l'application n'a jamais besoin
d'importer alpyca.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class DeviceKind(str, Enum):
    MOUNT = "telescope"     # nom Alpaca du type d'appareil
    CAMERA = "camera"


class Epoch(str, Enum):
    JNOW = "jnow"           # coordonnées « du jour » (ce qu'utilisent la plupart des montures)
    J2000 = "j2000"         # catalogues (≈ ICRS)


class TrackingMode(str, Enum):
    STOPPED = "stopped"
    SIDEREAL = "sidereal"
    LUNAR = "lunar"
    SOLAR = "solar"
    KING = "king"
    CUSTOM = "custom"       # sidéral + décalages ra_rate / dec_rate


class GuideDirection(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"


class Axis(str, Enum):
    PRIMARY = "primary"     # AD ou azimut
    SECONDARY = "secondary" # Dec ou altitude


class PierSide(str, Enum):
    EAST = "east"
    WEST = "west"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Coordinates:
    ra_hours: float         # [0, 24)
    dec_degrees: float      # [-90, 90]
    epoch: Epoch = Epoch.JNOW


@dataclass(frozen=True)
class DeviceInfo:
    id: str                 # "hôte:port/type/numéro", ex. "127.0.0.1:32323/telescope/0"
    kind: DeviceKind
    name: str
    server: str             # "hôte:port" du serveur Alpaca
    number: int
    unique_id: str = ""


@dataclass(frozen=True)
class MountInfo:
    device: DeviceInfo
    can_slew: bool
    can_sync: bool
    can_park: bool
    can_unpark: bool
    can_pulse_guide: bool
    can_set_tracking: bool
    can_set_custom_rate: bool
    can_move_axis: bool
    guide_rate_ra_arcsec_per_s: Optional[float]
    guide_rate_dec_arcsec_per_s: Optional[float]
    site_latitude_deg: Optional[float]
    site_longitude_deg: Optional[float]
    native_epoch: Epoch     # repère dans lequel la monture travaille


@dataclass(frozen=True)
class MountState:
    device_id: str
    coordinates: Coordinates
    altitude_deg: float
    azimuth_deg: float
    sidereal_time_hours: float
    side_of_pier: PierSide
    slewing: bool
    tracking: bool
    tracking_mode: TrackingMode
    parked: bool
    pulse_guiding: bool
    timestamp: dt.datetime  # UTC, heure de lecture


@dataclass(frozen=True)
class CameraInfo:
    device: DeviceInfo
    sensor_name: str
    width_px: int
    height_px: int
    pixel_size_um_x: float
    pixel_size_um_y: float
    max_adu: int
    gain_min: Optional[int]
    gain_max: Optional[int]
    exposure_min_s: float
    exposure_max_s: float
    max_bin_x: int
    max_bin_y: int
    can_set_gain: bool
    can_cool: bool
    can_abort: bool


@dataclass(frozen=True)
class CameraStatus:
    device_id: str
    state: str              # idle | waiting | exposing | reading | download | error
    temperature_c: Optional[float]
    cooler_on: Optional[bool]
    cooler_power_percent: Optional[float]
    gain: Optional[int]
    bin_x: int
    bin_y: int


@dataclass(frozen=True)
class ExposureResult:
    fits_path: Path         # chemin absolu du FITS écrit
    width_px: int
    height_px: int
    start_time: dt.datetime # UTC
    exposure_s: float
    temperature_c: Optional[float]
