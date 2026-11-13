#!/usr/bin/env python3
"""Simulateur du service de périphériques (monture + caméra).

Il implémente EXACTEMENT le contrat de proto/startracker/devices/v1/devices.proto.
Usage :
  - le reste de l'équipe développe et teste le backend Python contre ce simulateur,
    sans matériel et sans le service C# ;
  - plus tard, le service C# (NINA.Equipment) remplace ce serveur sans changer le client.

Lancer :  python sim_server.py [--port 50051]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
import threading
import time
from concurrent import futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "generated"))

import grpc  # noqa: E402
import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402
from google.protobuf import empty_pb2, timestamp_pb2  # noqa: E402

from startracker.devices.v1 import devices_pb2 as pb  # noqa: E402
from startracker.devices.v1 import devices_pb2_grpc as pbg  # noqa: E402

SIDEREAL_RATE_H_PER_S = 24.0 / 86164.0905          # heures d'AD par seconde SI
SIDEREAL_SEC_PER_SI_SEC = 86400.0 / 86164.0905     # ≈ 1.0027379
GUIDE_RATE_ARCSEC_PER_S = 0.5 * 15.041             # 0.5x sidéral, valeur courante
SLEW_SPEED_DEG_PER_S = 4.0


def now_ts() -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.GetCurrentTime()
    return ts


def lst_hours(lon_deg: float) -> float:
    """Temps sidéral local approché (suffisant pour un simulateur)."""
    jd = dt.datetime.now(dt.timezone.utc).timestamp() / 86400.0 + 2440587.5
    gmst = (18.697374558 + 24.06570982441908 * (jd - 2451545.0)) % 24.0
    return (gmst + lon_deg / 15.0) % 24.0


def ang_sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    a1, a2 = math.radians(ra1_h * 15), math.radians(ra2_h * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# ───────────────────────── Monture simulée ─────────────────────────

class SimMount:
    id = "sim-mount"
    name = "Simulated Mount"

    def __init__(self, lat: float = 48.82, lon: float = 2.39) -> None:
        self.lat, self.lon = lat, lon
        self.lock = threading.RLock()
        self.connected = False
        self.parked = True
        self.tracking_mode = pb.TRACKING_MODE_STOPPED
        self.custom_ra = 0.0
        self.custom_dec = 0.0
        self.axis_rate = [0.0, 0.0]              # deg/s (primaire, secondaire)
        self.ra = lst_hours(lon)                 # parqué au méridien
        self.dec = lat
        self._t = time.monotonic()
        self._pulse_until = 0.0
        self._slew: dict | None = None

    # -- modèle physique --
    def _advance(self) -> None:
        now = time.monotonic()
        dt_ = now - self._t
        self._t = now
        if self._slew is not None:
            return
        if self.parked or self.tracking_mode == pb.TRACKING_MODE_STOPPED:
            # monture immobile : le ciel défile, l'AD pointée augmente
            self.ra += SIDEREAL_RATE_H_PER_S * dt_
        elif self.tracking_mode == pb.TRACKING_MODE_CUSTOM:
            self.ra += self.custom_ra * SIDEREAL_SEC_PER_SI_SEC * dt_ / 3600.0
            self.dec += self.custom_dec * dt_ / 3600.0
        self.ra += self.axis_rate[0] * dt_ / 15.0
        self.dec += self.axis_rate[1] * dt_
        self.ra %= 24.0
        self.dec = max(-90.0, min(90.0, self.dec))

    def _position(self) -> tuple[float, float]:
        if self._slew is None:
            return self.ra, self.dec
        s = self._slew
        f = min(1.0, (time.monotonic() - s["t0"]) / s["duration"])
        dra = (s["target"][0] - s["start"][0] + 12.0) % 24.0 - 12.0   # chemin le plus court
        return (s["start"][0] + dra * f) % 24.0, s["start"][1] + (s["target"][1] - s["start"][1]) * f

    def state(self) -> pb.MountState:
        with self.lock:
            self._advance()
            ra, dec = self._position()
            lst = lst_hours(self.lon)
            ha = math.radians(((lst - ra + 12.0) % 24.0 - 12.0) * 15.0)
            lat, d = math.radians(self.lat), math.radians(dec)
            alt = math.degrees(math.asin(math.sin(d) * math.sin(lat) + math.cos(d) * math.cos(lat) * math.cos(ha)))
            az = (math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(lat) - math.tan(d) * math.cos(lat))) + 180.0) % 360.0
            tracking = (not self.parked) and self.tracking_mode != pb.TRACKING_MODE_STOPPED
            return pb.MountState(
                device_id=self.id,
                coordinates=pb.Coordinates(ra_hours=ra, dec_degrees=dec, epoch=pb.EPOCH_JNOW),
                altitude_deg=alt, azimuth_deg=az, sidereal_time_hours=lst,
                side_of_pier=pb.PIER_SIDE_EAST if ha > 0 else pb.PIER_SIDE_WEST,
                slewing=self._slew is not None, tracking=tracking,
                tracking_mode=self.tracking_mode, parked=self.parked,
                pulse_guiding=time.monotonic() < self._pulse_until,
                timestamp=now_ts(),
            )

    def info(self) -> pb.DeviceInfo:
        return pb.DeviceInfo(id=self.id, kind=pb.DEVICE_KIND_MOUNT, name=self.name,
                             backend="simulator", connected=self.connected)

    # -- commandes --
    def slew(self, ra: float, dec: float) -> bool:
        with self.lock:
            self._advance()
            start = self._position()
            dist = ang_sep_deg(start[0], start[1], ra, dec)
            ev = threading.Event()
            self._slew = {"start": start, "target": (ra % 24.0, dec), "t0": time.monotonic(),
                          "duration": max(0.5, dist / SLEW_SPEED_DEG_PER_S), "abort": ev}
            duration = self._slew["duration"]
        completed = not ev.wait(duration)
        with self.lock:
            if self._slew is not None and self._slew["abort"] is ev:
                self.ra, self.dec = self._position() if not completed else self._slew["target"]
                self._slew = None
                self._t = time.monotonic()
        return completed

    def stop_slew(self) -> None:
        with self.lock:
            if self._slew is not None:
                self._slew["abort"].set()
            self.axis_rate = [0.0, 0.0]

    def pulse_guide(self, direction: int, duration_ms: int) -> None:
        with self.lock:
            self._advance()
            arcsec = GUIDE_RATE_ARCSEC_PER_S * duration_ms / 1000.0
            if direction == pb.GUIDE_DIRECTION_NORTH:
                self.dec += arcsec / 3600.0
            elif direction == pb.GUIDE_DIRECTION_SOUTH:
                self.dec -= arcsec / 3600.0
            elif direction == pb.GUIDE_DIRECTION_EAST:
                self.ra += arcsec / 15.0 / 3600.0
            elif direction == pb.GUIDE_DIRECTION_WEST:
                self.ra -= arcsec / 15.0 / 3600.0
            self.ra %= 24.0
            self.dec = max(-90.0, min(90.0, self.dec))
            self._pulse_until = time.monotonic() + duration_ms / 1000.0


# ───────────────────────── Caméra simulée ─────────────────────────

class SimCamera:
    id = "sim-camera"
    name = "Simulated Camera"
    W, H, BITS = 1280, 960, 16
    GAIN_MIN, GAIN_MAX = 0, 500
    EXP_MIN, EXP_MAX = 0.001, 3600.0
    MAX_BIN = 4
    AMBIENT_C = 20.0

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.connected = False
        self.gain, self.offset, self.bin_x, self.bin_y = 100, 10, 1, 1
        self.cooler_on, self.target_c = False, 0.0
        self._temp, self._temp_t = self.AMBIENT_C, time.monotonic()
        self._abort: threading.Event | None = None
        rng = np.random.default_rng(42)           # même "ciel" à chaque image
        self._stars = [(rng.uniform(0, self.W), rng.uniform(0, self.H), rng.uniform(2e3, 3e4)) for _ in range(60)]

    def info(self) -> pb.DeviceInfo:
        return pb.DeviceInfo(id=self.id, kind=pb.DEVICE_KIND_CAMERA, name=self.name,
                             backend="simulator", connected=self.connected)

    @property
    def temperature(self) -> float:
        with self.lock:
            now = time.monotonic()
            goal = self.target_c if self.cooler_on else self.AMBIENT_C
            k = 1.0 - math.exp(-(now - self._temp_t) / 30.0)      # constante de temps 30 s
            self._temp += (goal - self._temp) * k
            self._temp_t = now
            return self._temp

    def status(self) -> pb.CameraStatus:
        t = self.temperature
        return pb.CameraStatus(
            device_id=self.id, temperature_c=t, cooler_on=self.cooler_on,
            cooler_power_percent=min(100.0, max(0.0, (self.AMBIENT_C - t) * 2.5)) if self.cooler_on else 0.0,
            exposing=self._abort is not None, gain=self.gain, bin_x=self.bin_x, bin_y=self.bin_y)

    def render(self, exposure_s: float) -> np.ndarray:
        w, h = self.W // self.bin_x, self.H // self.bin_y
        rng = np.random.default_rng()
        gain_f = 1.0 + self.gain / 100.0
        img = rng.normal(self.offset * 10 + 500, 8.0 + 0.5 * gain_f, (h, w))
        yy, xx = np.mgrid[0:h, 0:w]
        for x, y, flux in self._stars:
            sx, sy = x / self.bin_x, y / self.bin_y
            img += flux * min(exposure_s, 10.0) / 10.0 * gain_f / self.bin_x * np.exp(
                -((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * (2.0 / self.bin_x) ** 2))
        return np.clip(img, 0, 65535).astype(np.uint16)


# ───────────────────────── Services gRPC ─────────────────────────

class Registry:
    def __init__(self) -> None:
        self.mount = SimMount()
        self.camera = SimCamera()
        self.devices = {self.mount.id: self.mount, self.camera.id: self.camera}

    def get(self, ctx: grpc.ServicerContext, device_id: str, kind_cls: type, need_connected: bool = True):
        dev = self.devices.get(device_id)
        if dev is None or not isinstance(dev, kind_cls):
            ctx.abort(grpc.StatusCode.NOT_FOUND, f"device_id inconnu : {device_id!r}")
        if need_connected and not dev.connected:
            ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, f"{device_id} n'est pas connecté")
        return dev


class DeviceServicer(pbg.DeviceServiceServicer):
    def __init__(self, reg: Registry) -> None:
        self.reg = reg

    def ListDevices(self, request, context):
        devs = [d.info() for d in self.reg.devices.values()
                if request.kind in (pb.DEVICE_KIND_UNSPECIFIED, d.info().kind)]
        return pb.ListDevicesResponse(devices=devs)

    def Connect(self, request, context):
        dev = self.reg.get(context, request.device_id, (SimMount, SimCamera), need_connected=False)
        dev.connected = True
        return dev.info()

    def Disconnect(self, request, context):
        dev = self.reg.get(context, request.device_id, (SimMount, SimCamera), need_connected=False)
        if isinstance(dev, SimCamera) and dev._abort is not None:
            dev._abort.set()
        if isinstance(dev, SimMount):
            dev.stop_slew()
        dev.connected = False
        return dev.info()


class MountServicer(pbg.MountServiceServicer):
    def __init__(self, reg: Registry) -> None:
        self.reg = reg

    def _m(self, ctx, device_id: str) -> SimMount:
        return self.reg.get(ctx, device_id, SimMount)

    def GetInfo(self, request, context):
        m = self._m(context, request.device_id)
        return pb.MountInfo(device=m.info(), can_slew=True, can_sync=True, can_park=True,
                            can_pulse_guide=True, can_set_tracking=True, can_set_custom_rate=True,
                            can_move_axis=True, guide_rate_ra_arcsec_per_sec=GUIDE_RATE_ARCSEC_PER_S,
                            guide_rate_dec_arcsec_per_sec=GUIDE_RATE_ARCSEC_PER_S,
                            site_latitude_deg=m.lat, site_longitude_deg=m.lon)

    def GetState(self, request, context):
        return self._m(context, request.device_id).state()

    def StreamState(self, request, context):
        m = self._m(context, request.device_id)
        period = (request.interval_ms or 500) / 1000.0
        while context.is_active() and m.connected:
            yield m.state()
            time.sleep(period)

    def SlewToCoordinates(self, request, context):
        m = self._m(context, request.device_id)
        t = request.target
        if not (0.0 <= t.ra_hours < 24.0) or not (-90.0 <= t.dec_degrees <= 90.0):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "coordonnées hors limites")
        if m.parked:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "monture parquée")
        if m.tracking_mode == pb.TRACKING_MODE_STOPPED:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                          "activer le suivi avant un slew (comportement de nombreux drivers ASCOM)")
        if m.state().slewing:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "un slew est déjà en cours")
        done = m.slew(t.ra_hours, t.dec_degrees)
        if not done:
            context.abort(grpc.StatusCode.ABORTED, "slew interrompu")
        return pb.SlewResult(completed=True, final_state=m.state())

    def StopSlew(self, request, context):
        self._m(context, request.device_id).stop_slew()
        return empty_pb2.Empty()

    def Sync(self, request, context):
        m = self._m(context, request.device_id)
        c = request.coordinates
        if not (0.0 <= c.ra_hours < 24.0) or not (-90.0 <= c.dec_degrees <= 90.0):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "coordonnées hors limites")
        with m.lock:
            m._advance()
            m.ra, m.dec = c.ra_hours, c.dec_degrees
        return empty_pb2.Empty()

    def SetTracking(self, request, context):
        m = self._m(context, request.device_id)
        if request.mode == pb.TRACKING_MODE_UNSPECIFIED:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "mode de suivi non précisé")
        if m.parked and request.mode != pb.TRACKING_MODE_STOPPED:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "monture parquée")
        with m.lock:
            m._advance()
            m.tracking_mode = request.mode
            m.custom_ra, m.custom_dec = (request.ra_rate, request.dec_rate) if request.mode == pb.TRACKING_MODE_CUSTOM else (0.0, 0.0)
        return m.state()

    def PulseGuide(self, request, context):
        m = self._m(context, request.device_id)
        if m.parked:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "monture parquée")
        if request.direction == pb.GUIDE_DIRECTION_UNSPECIFIED or not (0 < request.duration_ms <= 10_000):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "direction ou durée invalide (1..10000 ms)")
        m.pulse_guide(request.direction, request.duration_ms)
        return empty_pb2.Empty()

    def MoveAxis(self, request, context):
        m = self._m(context, request.device_id)
        if m.parked:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "monture parquée")
        if request.axis not in (pb.AXIS_PRIMARY, pb.AXIS_SECONDARY) or abs(request.rate_deg_per_sec) > 5.0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "axe ou vitesse invalide (|v| <= 5 deg/s)")
        with m.lock:
            m._advance()
            m.axis_rate[0 if request.axis == pb.AXIS_PRIMARY else 1] = request.rate_deg_per_sec
        return empty_pb2.Empty()

    def Park(self, request, context):
        m = self._m(context, request.device_id)
        m.stop_slew()
        with m.lock:
            m._advance()
            m.parked, m.tracking_mode = True, pb.TRACKING_MODE_STOPPED
        return m.state()

    def Unpark(self, request, context):
        m = self._m(context, request.device_id)
        with m.lock:
            m._advance()
            m.parked = False
        return m.state()


class CameraServicer(pbg.CameraServiceServicer):
    def __init__(self, reg: Registry) -> None:
        self.reg = reg

    def _c(self, ctx, device_id: str) -> SimCamera:
        return self.reg.get(ctx, device_id, SimCamera)

    def GetInfo(self, request, context):
        c = self._c(context, request.device_id)
        return pb.CameraInfo(device=c.info(), sensor_name="SIM-IMX000", width_px=c.W, height_px=c.H,
                             pixel_size_um_x=3.76, pixel_size_um_y=3.76, bit_depth=c.BITS,
                             gain_min=c.GAIN_MIN, gain_max=c.GAIN_MAX, exposure_min_s=c.EXP_MIN,
                             exposure_max_s=c.EXP_MAX, max_bin=c.MAX_BIN, can_set_gain=True,
                             can_cool=True, can_subframe=False)

    def GetStatus(self, request, context):
        return self._c(context, request.device_id).status()

    def SetSettings(self, request, context):
        c = self._c(context, request.device_id)
        if c._abort is not None:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, "pose en cours")
        if request.HasField("gain") and not (c.GAIN_MIN <= request.gain <= c.GAIN_MAX):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"gain hors limites [{c.GAIN_MIN}, {c.GAIN_MAX}]")
        for f in ("bin_x", "bin_y"):
            if request.HasField(f) and not (1 <= getattr(request, f) <= c.MAX_BIN):
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"{f} hors limites [1, {c.MAX_BIN}]")
        with c.lock:
            _ = c.temperature                       # fige la température avant de changer la consigne
            if request.HasField("gain"): c.gain = request.gain
            if request.HasField("offset"): c.offset = request.offset
            if request.HasField("bin_x"): c.bin_x = request.bin_x
            if request.HasField("bin_y"): c.bin_y = request.bin_y
            if request.HasField("target_temperature_c"): c.target_c = request.target_temperature_c
            if request.HasField("cooler_on"): c.cooler_on = request.cooler_on
        return c.status()

    def Expose(self, request, context):
        c = self._c(context, request.device_id)
        if not (c.EXP_MIN <= request.exposure_s <= c.EXP_MAX):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"pose hors limites [{c.EXP_MIN}, {c.EXP_MAX}] s")
        out = Path(request.output_dir)
        if not out.is_dir():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"output_dir inexistant : {request.output_dir!r}")
        with c.lock:
            if c._abort is not None:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "pose déjà en cours")
            abort = c._abort = threading.Event()
        start = dt.datetime.now(dt.timezone.utc)
        try:
            t0 = time.monotonic()
            while True:
                elapsed = time.monotonic() - t0
                if abort.is_set() or not context.is_active():
                    context.abort(grpc.StatusCode.ABORTED, "pose interrompue")
                if elapsed >= request.exposure_s:
                    break
                yield pb.ExposeEvent(progress=pb.ExposureProgress(elapsed_s=elapsed, total_s=request.exposure_s))
                abort.wait(min(0.25, request.exposure_s - elapsed))
            data = c.render(request.exposure_s)
            stem = request.filename_stem or "sim_" + start.strftime("%Y%m%dT%H%M%S_%f")
            path = (out / f"{stem}.fits").resolve()
            hdr = fits.Header()
            hdr["EXPTIME"] = (request.exposure_s, "[s] exposure time")
            hdr["DATE-OBS"] = start.strftime("%Y-%m-%dT%H:%M:%S.%f")
            hdr["GAIN"], hdr["OFFSET"] = c.gain, c.offset
            hdr["XBINNING"], hdr["YBINNING"] = c.bin_x, c.bin_y
            hdr["CCD-TEMP"] = (round(c.temperature, 2), "[C] sensor temperature")
            hdr["INSTRUME"] = c.name
            fits.PrimaryHDU(data=data, header=hdr).writeto(path, overwrite=False)
            start_ts = timestamp_pb2.Timestamp()
            start_ts.FromDatetime(start.replace(tzinfo=None))
            yield pb.ExposeEvent(result=pb.ExposureResult(
                fits_path=str(path), width_px=data.shape[1], height_px=data.shape[0], bit_depth=c.BITS,
                start_time=start_ts, exposure_s=request.exposure_s, temperature_c=c.temperature))
        finally:
            with c.lock:
                c._abort = None

    def AbortExposure(self, request, context):
        c = self._c(context, request.device_id)
        if c._abort is not None:
            c._abort.set()
        return empty_pb2.Empty()


def build_server(port: int = 50051, host: str = "127.0.0.1") -> tuple[grpc.Server, int]:
    reg = Registry()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    pbg.add_DeviceServiceServicer_to_server(DeviceServicer(reg), server)
    pbg.add_MountServiceServicer_to_server(MountServicer(reg), server)
    pbg.add_CameraServiceServicer_to_server(CameraServicer(reg), server)
    bound = server.add_insecure_port(f"{host}:{port}")
    return server, bound


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=50051)
    args = ap.parse_args()
    srv, port = build_server(args.port)
    srv.start()
    print(f"Simulateur à l'écoute sur 127.0.0.1:{port} (Ctrl+C pour arrêter)")
    try:
        srv.wait_for_termination()
    except KeyboardInterrupt:
        srv.stop(grace=1)
