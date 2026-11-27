"""Faux serveur Alpaca (monture + caméra) pour les tests, en Python pur.

Il implémente le sous-ensemble de l'API Alpaca utilisé par startracker_devices,
avec les mêmes codes d'erreur que la norme, pour tester sans Windows ni OmniSim.
Ce n'est PAS une référence : le vrai test reste OmniSim (voir README).

Lancer à la main :  python tests/fake_alpaca.py --port 32323
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

import numpy as np

SIDEREAL_H_PER_S = 24.0 / 86164.0905           # heures d'AD par seconde SI
SIDEREAL_SEC_PER_SI_SEC = 86400.0 / 86164.0905
GUIDE_RATE_DEG_S = 0.5 * 15.041 / 3600.0        # 0,5 x sidéral
SLEW_DEG_S = 4.0

NOT_IMPLEMENTED, INVALID_VALUE, NOT_CONNECTED, PARKED, INVALID_OPERATION = 0x400, 0x401, 0x407, 0x408, 0x40B


class AlpacaError(Exception):
    def __init__(self, number: int, message: str) -> None:
        super().__init__(message)
        self.number, self.message = number, message


def _bool(v: str) -> bool:
    if str(v).lower() not in ("true", "false"):
        raise AlpacaError(INVALID_VALUE, f"booléen invalide : {v!r}")
    return str(v).lower() == "true"


def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        raise AlpacaError(INVALID_VALUE, f"nombre invalide : {v!r}")


def lst_hours(lon_deg: float) -> float:
    jd = time.time() / 86400.0 + 2440587.5
    return (18.697374558 + 24.06570982441908 * (jd - 2451545.0) + lon_deg / 15.0) % 24.0


def ang_sep_deg(ra1, d1, ra2, d2) -> float:
    a1, a2, d1, d2 = map(math.radians, (ra1 * 15, ra2 * 15, d1, d2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(a1 - a2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


# ───────────────────────── Monture ─────────────────────────

class FakeTelescope:
    ALWAYS = {"interfaceversion", "name", "description", "driverinfo", "driverversion",
              "supportedactions", "connected", "connecting", "connect", "disconnect"}

    def __init__(self, lat=48.82, lon=2.39) -> None:
        self.lock = threading.RLock()
        self.lat, self.lon = lat, lon
        self.connected = False
        self.parked = True
        self.tracking = False
        self.rate = 0
        self.ra_rate = self.dec_rate = 0.0
        self.axis = [0.0, 0.0]
        self.ra, self.dec = lst_hours(lon), lat       # parquée au méridien
        self._t = time.monotonic()
        self._pulse_until = 0.0
        self._slew = None

    def _advance(self) -> None:
        now = time.monotonic()
        dt_ = now - self._t
        self._t = now
        if self._slew is not None:
            if now >= self._slew["t0"] + self._slew["dur"]:
                self.ra, self.dec = self._slew["target"]
                self._slew = None
            return
        if not self.tracking:
            self.ra += SIDEREAL_H_PER_S * dt_          # le ciel défile
        else:
            self.ra += self.ra_rate * SIDEREAL_SEC_PER_SI_SEC * dt_ / 3600.0
            self.dec += self.dec_rate * dt_ / 3600.0
        self.ra += self.axis[0] * dt_ / 15.0
        self.dec += self.axis[1] * dt_
        self.ra %= 24.0
        self.dec = max(-90.0, min(90.0, self.dec))

    def _pos(self):
        s = self._slew
        if s is None:
            return self.ra, self.dec
        f = min(1.0, (time.monotonic() - s["t0"]) / s["dur"])
        dra = (s["target"][0] - s["start"][0] + 12.0) % 24.0 - 12.0
        return (s["start"][0] + dra * f) % 24.0, s["start"][1] + (s["target"][1] - s["start"][1]) * f

    def _altaz(self, ra, dec):
        ha = math.radians(((lst_hours(self.lon) - ra + 12.0) % 24.0 - 12.0) * 15.0)
        lat, d = math.radians(self.lat), math.radians(dec)
        alt = math.degrees(math.asin(math.sin(d) * math.sin(lat) + math.cos(d) * math.cos(lat) * math.cos(ha)))
        az = (math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(lat) - math.tan(d) * math.cos(lat))) + 180.0) % 360.0
        return alt, az, ha

    def _not_parked(self):
        if self.parked:
            raise AlpacaError(PARKED, "la monture est parquée")

    def handle(self, verb: str, method: str, p: dict):
        with self.lock:
            if method not in self.ALWAYS and not self.connected:
                raise AlpacaError(NOT_CONNECTED, "monture non connectée")
            self._advance()
            ra, dec = self._pos()
            if verb == "GET":
                alt, az, ha = self._altaz(ra, dec)
                values = {
                    "interfaceversion": 4, "name": "Fake Telescope", "description": "Monture simulée",
                    "driverinfo": "fake_alpaca.py", "driverversion": "1.0", "supportedactions": [],
                    "connected": self.connected, "connecting": False,
                    "rightascension": ra, "declination": dec, "altitude": alt, "azimuth": az,
                    "siderealtime": lst_hours(self.lon), "sideofpier": 0 if ha > 0 else 1,
                    "slewing": self._slew is not None or any(self.axis), "tracking": self.tracking,
                    "trackingrate": self.rate, "trackingrates": [0, 1, 2],
                    "rightascensionrate": self.ra_rate, "declinationrate": self.dec_rate,
                    "atpark": self.parked, "ispulseguiding": time.monotonic() < self._pulse_until,
                    "sitelatitude": self.lat, "sitelongitude": self.lon, "equatorialsystem": 1,
                    "canpark": True, "canunpark": True, "cansync": True, "canslew": True, "canslewasync": True,
                    "canpulseguide": True, "cansettracking": True, "cansetrightascensionrate": True,
                    "cansetdeclinationrate": True, "canmoveaxis": int(p.get("axis", 9)) in (0, 1),
                    "axisrates": [{"Minimum": 0.0, "Maximum": 5.0}] if int(p.get("axis", 9)) in (0, 1) else [],
                    "guideraterightascension": GUIDE_RATE_DEG_S, "guideratedeclination": GUIDE_RATE_DEG_S,
                }
                if method not in values:
                    raise AlpacaError(NOT_IMPLEMENTED, f"{method} non implémenté")
                return values[method]

            # PUT
            if method == "connect" or (method == "connected" and _bool(p.get("connected"))):
                self.connected = True
            elif method == "disconnect" or method == "connected":
                self.connected = False
                self._slew = None
            elif method == "tracking":
                on = _bool(p.get("tracking"))
                if on:
                    self._not_parked()
                self.tracking = on
            elif method == "trackingrate":
                r = _num(p.get("trackingrate"), int)
                if r not in (0, 1, 2):
                    raise AlpacaError(INVALID_VALUE, "vitesse de suivi non supportée")
                self.rate = r
            elif method == "rightascensionrate":
                if self.rate != 0:
                    raise AlpacaError(INVALID_OPERATION, "décalage possible seulement en sidéral")
                self.ra_rate = _num(p.get("rightascensionrate"))
            elif method == "declinationrate":
                if self.rate != 0:
                    raise AlpacaError(INVALID_OPERATION, "décalage possible seulement en sidéral")
                self.dec_rate = _num(p.get("declinationrate"))
            elif method == "slewtocoordinatesasync":
                tra, tdec = _num(p.get("rightascension")), _num(p.get("declination"))
                if not (0 <= tra < 24 and -90 <= tdec <= 90):
                    raise AlpacaError(INVALID_VALUE, "coordonnées hors limites")
                self._not_parked()
                if not self.tracking:
                    raise AlpacaError(INVALID_OPERATION, "le suivi doit être actif pour pointer")
                dist = ang_sep_deg(ra, dec, tra, tdec)
                self._slew = {"start": (ra, dec), "target": (tra, tdec), "t0": time.monotonic(),
                              "dur": max(0.5, dist / SLEW_DEG_S)}
            elif method == "abortslew":
                self._not_parked()
                if self._slew is not None:
                    self.ra, self.dec = ra, dec
                    self._slew = None
                self.axis = [0.0, 0.0]
            elif method == "synctocoordinates":
                self._not_parked()
                sra, sdec = _num(p.get("rightascension")), _num(p.get("declination"))
                if not (0 <= sra < 24 and -90 <= sdec <= 90):
                    raise AlpacaError(INVALID_VALUE, "coordonnées hors limites")
                self.ra, self.dec = sra, sdec
            elif method == "pulseguide":
                self._not_parked()
                d, ms = _num(p.get("direction"), int), _num(p.get("duration"), int)
                if d not in (0, 1, 2, 3) or ms < 0:
                    raise AlpacaError(INVALID_VALUE, "direction ou durée invalide")
                deg = GUIDE_RATE_DEG_S * ms / 1000.0
                if d in (0, 1):
                    self.dec += deg if d == 0 else -deg
                else:
                    self.ra = (self.ra + (deg if d == 2 else -deg) / 15.0) % 24.0
                self._pulse_until = time.monotonic() + ms / 1000.0
            elif method == "moveaxis":
                self._not_parked()
                a, r = _num(p.get("axis"), int), _num(p.get("rate"))
                if a not in (0, 1) or abs(r) > 5.0:
                    raise AlpacaError(INVALID_VALUE, "axe ou vitesse invalide")
                self.axis[a] = r
            elif method == "park":
                self._slew = None
                self.axis = [0.0, 0.0]
                self.parked, self.tracking = True, False
            elif method == "unpark":
                self.parked = False
            else:
                raise AlpacaError(NOT_IMPLEMENTED, f"{method} non implémenté")
            return None


# ───────────────────────── Caméra ─────────────────────────

class FakeCamera:
    ALWAYS = FakeTelescope.ALWAYS
    W, H, MAX_BIN = 1280, 960, 4

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.connected = False
        self.gain, self.offset = 100, 10
        self.binx = self.biny = 1
        self.numx, self.numy, self.startx, self.starty = self.W, self.H, 0, 0
        self.cooler_on, self.target_c = False, 0.0
        self._temp, self._temp_t = 20.0, time.monotonic()
        self._exp = None                 # {"t0", "dur", "start_utc", "light"}
        self.image = None
        self.last = (None, None)         # (start_utc, durée)
        rng = np.random.default_rng(42)
        self._stars = [(rng.uniform(0, self.W), rng.uniform(0, self.H), rng.uniform(2e3, 3e4)) for _ in range(60)]

    def _temperature(self) -> float:
        now = time.monotonic()
        goal = self.target_c if self.cooler_on else 20.0
        self._temp += (goal - self._temp) * (1.0 - math.exp(-(now - self._temp_t) / 30.0))
        self._temp_t = now
        return self._temp

    def _tick(self) -> None:
        e = self._exp
        if e is not None and time.monotonic() >= e["t0"] + e["dur"]:
            self.image = self._render(e["dur"])
            self.last = (e["start_utc"], e["dur"])
            self._exp = None

    def _render(self, exposure_s: float) -> np.ndarray:
        w, h = self.numx, self.numy
        g = 1.0 + self.gain / 100.0
        img = np.random.default_rng().normal(self.offset * 10 + 500, 8.0 + 0.5 * g, (h, w))
        yy, xx = np.mgrid[0:h, 0:w]
        for x, y, flux in self._stars:
            sx, sy = x / self.binx - self.startx, y / self.biny - self.starty
            if -10 <= sx <= w + 10 and -10 <= sy <= h + 10:
                img += flux * min(exposure_s, 10.0) / 10.0 * g * np.exp(
                    -((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * (2.0 / self.binx) ** 2))
        return np.clip(img, 0, 65535).astype(np.uint16)

    def handle(self, verb: str, method: str, p: dict):
        with self.lock:
            if method not in self.ALWAYS and not self.connected:
                raise AlpacaError(NOT_CONNECTED, "caméra non connectée")
            self._tick()
            busy = self._exp is not None
            if verb == "GET":
                start, dur = self.last
                values = {
                    "interfaceversion": 4, "name": "Fake Camera", "description": "Caméra simulée",
                    "driverinfo": "fake_alpaca.py", "driverversion": "1.0", "supportedactions": [],
                    "connected": self.connected, "connecting": False,
                    "camerastate": 2 if busy else 0, "cameraxsize": self.W, "cameraysize": self.H,
                    "pixelsizex": 3.76, "pixelsizey": 3.76, "maxadu": 65535, "sensorname": "FAKE-IMX",
                    "sensortype": 0, "gain": self.gain, "gainmin": 0, "gainmax": 500,
                    "offset": self.offset, "offsetmin": 0, "offsetmax": 255,
                    "binx": self.binx, "biny": self.biny, "maxbinx": self.MAX_BIN, "maxbiny": self.MAX_BIN,
                    "numx": self.numx, "numy": self.numy, "startx": self.startx, "starty": self.starty,
                    "exposuremin": 0.001, "exposuremax": 3600.0,
                    "cansetccdtemperature": True, "cangetcoolerpower": True,
                    "ccdtemperature": self._temperature(), "setccdtemperature": self.target_c,
                    "cooleron": self.cooler_on,
                    "coolerpower": min(100.0, max(0.0, (20.0 - self._temperature()) * 2.5)) if self.cooler_on else 0.0,
                    "canabortexposure": True, "canstopexposure": True,
                    "imageready": self.image is not None and not busy,
                    "percentcompleted": 0 if not busy else int(100 * (time.monotonic() - self._exp["t0"]) / self._exp["dur"]),
                }
                if method == "lastexposurestarttime" or method == "lastexposureduration":
                    if start is None:
                        raise AlpacaError(0x402, "aucune pose effectuée")
                    return start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] if method.endswith("time") else dur
                if method not in values:
                    raise AlpacaError(NOT_IMPLEMENTED, f"{method} non implémenté")
                return values[method]

            if method in ("connect", "connected", "disconnect"):
                self.connected = method == "connect" or (method == "connected" and _bool(p.get("connected")))
                if not self.connected:
                    self._exp = None
                return None
            if busy and method not in ("abortexposure", "stopexposure", "cooleron", "setccdtemperature"):
                raise AlpacaError(INVALID_OPERATION, "pose en cours")
            if method == "gain":
                v = _num(p.get("gain"), int)
                if not 0 <= v <= 500:
                    raise AlpacaError(INVALID_VALUE, "gain hors limites [0, 500]")
                self.gain = v
            elif method == "offset":
                v = _num(p.get("offset"), int)
                if not 0 <= v <= 255:
                    raise AlpacaError(INVALID_VALUE, "offset hors limites [0, 255]")
                self.offset = v
            elif method in ("binx", "biny"):
                v = _num(p.get(method), int)
                if not 1 <= v <= self.MAX_BIN:
                    raise AlpacaError(INVALID_VALUE, f"{method} hors limites [1, {self.MAX_BIN}]")
                setattr(self, method, v)
            elif method in ("numx", "numy", "startx", "starty"):
                setattr(self, method, _num(p.get(method), int))
            elif method == "setccdtemperature":
                self._temperature()
                self.target_c = _num(p.get("setccdtemperature"))
            elif method == "cooleron":
                self._temperature()
                self.cooler_on = _bool(p.get("cooleron"))
            elif method == "startexposure":
                d = _num(p.get("duration"))
                light = _bool(p.get("light"))
                if not 0.001 <= d <= 3600:
                    raise AlpacaError(INVALID_VALUE, "durée hors limites")
                if self.startx + self.numx > self.W // self.binx or self.starty + self.numy > self.H // self.biny:
                    raise AlpacaError(INVALID_VALUE, "sous-fenêtre hors capteur pour ce binning")
                self.image = None
                self._exp = {"t0": time.monotonic(), "dur": d, "light": light,
                             "start_utc": dt.datetime.now(dt.timezone.utc)}
            elif method in ("abortexposure", "stopexposure"):
                self._exp = None
                self.image = None
            else:
                raise AlpacaError(NOT_IMPLEMENTED, f"{method} non implémenté")
            return None

    def image_bytes(self) -> bytes:
        with self.lock:
            self._tick()
            if self.image is None:
                raise AlpacaError(INVALID_OPERATION, "aucune image disponible")
            xy = np.ascontiguousarray(self.image.T)        # Alpaca : [x][y]
            # En-tête ImageBytes v1 : 11 entiers 32 bits little-endian, données à l'octet 44.
            hdr = struct.pack("<11i", 1, 0, 0, 0, 44, 2, 8, 2, xy.shape[0], xy.shape[1], 0)
            return hdr + xy.astype("<u2").tobytes()

    def image_json(self):
        with self.lock:
            self._tick()
            if self.image is None:
                raise AlpacaError(INVALID_OPERATION, "aucune image disponible")
            return self.image.T.tolist()


# ───────────────────────── Serveur HTTP ─────────────────────────

class FakeAlpacaServer:
    def __init__(self, port: int = 0, host: str = "127.0.0.1", imagebytes: bool = True) -> None:
        self.telescope, self.camera = FakeTelescope(), FakeCamera()
        self.imagebytes = imagebytes
        self._stid = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):            # silencieux pendant les tests
                pass

            def do_GET(self):
                outer._dispatch(self, "GET")

            def do_PUT(self):
                outer._dispatch(self, "PUT")

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.address = f"{host}:{self.httpd.server_address[1]}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "FakeAlpacaServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def _send(self, h, code: int, body: bytes, ctype: str) -> None:
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def _dispatch(self, h, verb: str) -> None:
        url = urlparse(h.path)
        params = dict(parse_qsl(url.query))
        if verb == "PUT":
            n = int(h.headers.get("Content-Length") or 0)
            params.update(parse_qsl(h.rfile.read(n).decode()))
        p = {k.lower(): v for k, v in params.items()}
        self._stid += 1
        base = {"ClientTransactionID": int(p.get("clienttransactionid", 0) or 0),
                "ServerTransactionID": self._stid, "ErrorNumber": 0, "ErrorMessage": ""}
        parts = [x for x in url.path.split("/") if x]

        if parts[:1] == ["management"]:
            value = {
                ("management", "apiversions"): [1],
                ("management", "v1", "description"): {"ServerName": "Fake Alpaca", "Manufacturer": "tests",
                                                      "ManufacturerVersion": "1.0", "Location": "ici"},
                ("management", "v1", "configureddevices"): [
                    {"DeviceName": "Fake Telescope", "DeviceType": "Telescope", "DeviceNumber": 0, "UniqueID": "fake-tel-0"},
                    {"DeviceName": "Fake Camera", "DeviceType": "Camera", "DeviceNumber": 0, "UniqueID": "fake-cam-0"},
                ],
            }.get(tuple(parts))
            if value is None:
                return self._send(h, 404, b"not found", "text/plain")
            return self._send(h, 200, json.dumps({**base, "Value": value}).encode(), "application/json")

        if len(parts) != 5 or parts[:2] != ["api", "v1"]:
            return self._send(h, 400, b"URL Alpaca invalide", "text/plain")
        dtype, num, method = parts[2], parts[3], parts[4]
        dev = {"telescope": self.telescope, "camera": self.camera}.get(dtype) if num == "0" else None
        if dev is None:
            return self._send(h, 400, f"appareil inconnu : {dtype}/{num}".encode(), "text/plain")
        try:
            if dtype == "camera" and method == "imagearray" and verb == "GET":
                if not self.camera.connected:
                    raise AlpacaError(NOT_CONNECTED, "caméra non connectée")
                if self.imagebytes and "application/imagebytes" in (h.headers.get("Accept") or ""):
                    return self._send(h, 200, self.camera.image_bytes(), "application/imagebytes")
                body = {**base, "Type": 2, "Rank": 2, "Value": self.camera.image_json()}
                return self._send(h, 200, json.dumps(body).encode(), "application/json")
            value = dev.handle(verb, method, p)
            body = {**base} if verb == "PUT" else {**base, "Value": value}
        except AlpacaError as e:
            body = {**base, "ErrorNumber": e.number, "ErrorMessage": e.message}
        self._send(h, 200, json.dumps(body).encode(), "application/json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Faux serveur Alpaca (monture + caméra)")
    ap.add_argument("--port", type=int, default=32323)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = FakeAlpacaServer(args.port, args.host).start()
    print(f"Faux serveur Alpaca sur {srv.address} (Ctrl+C pour arrêter)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
