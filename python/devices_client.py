"""Client Python du service de périphériques.

C'est la seule chose que le reste du backend doit importer : il n'a jamais besoin de
connaître gRPC, ASCOM, NINA ou les SDK. Les codes d'erreur gRPC sont traduits en
exceptions Python explicites.

    with DevicesClient("127.0.0.1:50051") as client:
        mount = client.mount("sim-mount")
        mount.connect()
        mount.unpark()
        mount.set_tracking(TrackingMode.TRACKING_MODE_SIDEREAL)
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).parent / "generated"))

import grpc  # noqa: E402
from google.protobuf import empty_pb2  # noqa: E402

from startracker.devices.v1 import devices_pb2 as pb  # noqa: E402
from startracker.devices.v1 import devices_pb2_grpc as pbg  # noqa: E402

# Ré-exports pratiques pour que l'appelant n'importe pas devices_pb2
TrackingMode = pb.TrackingMode
GuideDirection = pb.GuideDirection
Axis = pb.Axis
Epoch = pb.Epoch
DeviceKind = pb.DeviceKind


# ───────────────────────── Exceptions ─────────────────────────

class DeviceError(Exception):
    """Erreur de base. `.detail` contient le message renvoyé par le service."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class DeviceNotFound(DeviceError): ...
class DeviceNotReady(DeviceError): """Non connecté, monture parquée, mauvais état."""
class NotSupported(DeviceError): """Capacité absente sur cet appareil (voir MountInfo.can_*)."""
class DeviceUnavailable(DeviceError): """Appareil ou service injoignable (câble, réseau)."""
class DeviceTimeout(DeviceError): ...
class OperationAborted(DeviceError): ...
class InvalidParameter(DeviceError): ...
class DeviceInternalError(DeviceError): ...


_CODE_TO_EXC = {
    grpc.StatusCode.NOT_FOUND: DeviceNotFound,
    grpc.StatusCode.FAILED_PRECONDITION: DeviceNotReady,
    grpc.StatusCode.UNIMPLEMENTED: NotSupported,
    grpc.StatusCode.UNAVAILABLE: DeviceUnavailable,
    grpc.StatusCode.DEADLINE_EXCEEDED: DeviceTimeout,
    grpc.StatusCode.ABORTED: OperationAborted,
    grpc.StatusCode.INVALID_ARGUMENT: InvalidParameter,
}


@contextmanager
def _translate() -> Iterator[None]:
    try:
        yield
    except grpc.RpcError as e:                      # les flux lèvent aussi RpcError
        exc = _CODE_TO_EXC.get(e.code(), DeviceInternalError)
        raise exc(e.details() or "") from e


def _ref(device_id: str) -> pb.DeviceRef:
    return pb.DeviceRef(device_id=device_id)


# ───────────────────────── Monture ─────────────────────────

class MountClient:
    def __init__(self, devices: pbg.DeviceServiceStub, stub: pbg.MountServiceStub, device_id: str) -> None:
        self._dev, self._s, self.id = devices, stub, device_id

    def connect(self) -> pb.DeviceInfo:
        with _translate():
            return self._dev.Connect(_ref(self.id))

    def disconnect(self) -> pb.DeviceInfo:
        with _translate():
            return self._dev.Disconnect(_ref(self.id))

    def info(self) -> pb.MountInfo:
        with _translate():
            return self._s.GetInfo(_ref(self.id))

    def state(self) -> pb.MountState:
        with _translate():
            return self._s.GetState(_ref(self.id))

    def stream_state(self, interval_ms: int = 500) -> Iterator[pb.MountState]:
        """Itérateur infini ; appeler .close() sur le générateur (ou break) pour arrêter."""
        call = self._s.StreamState(pb.StreamStateRequest(device_id=self.id, interval_ms=interval_ms))
        try:
            with _translate():
                for st in call:
                    yield st
        finally:
            call.cancel()

    def slew_to(self, ra_hours: float, dec_degrees: float, *, epoch: int = Epoch.EPOCH_JNOW,
                timeout_s: float = 300.0) -> pb.MountState:
        """Bloque jusqu'à la fin du pointage. Lève OperationAborted si stop_slew() est appelé."""
        req = pb.SlewRequest(device_id=self.id,
                             target=pb.Coordinates(ra_hours=ra_hours, dec_degrees=dec_degrees, epoch=epoch))
        with _translate():
            return self._s.SlewToCoordinates(req, timeout=timeout_s).final_state

    def stop_slew(self) -> None:
        with _translate():
            self._s.StopSlew(_ref(self.id))

    def sync(self, ra_hours: float, dec_degrees: float, *, epoch: int = Epoch.EPOCH_JNOW) -> None:
        with _translate():
            self._s.Sync(pb.SyncRequest(device_id=self.id, coordinates=pb.Coordinates(
                ra_hours=ra_hours, dec_degrees=dec_degrees, epoch=epoch)))

    def set_tracking(self, mode: int, ra_rate: float = 0.0, dec_rate: float = 0.0) -> pb.MountState:
        with _translate():
            return self._s.SetTracking(pb.SetTrackingRequest(
                device_id=self.id, mode=mode, ra_rate=ra_rate, dec_rate=dec_rate))

    def pulse_guide(self, direction: int, duration_ms: int) -> None:
        with _translate():
            self._s.PulseGuide(pb.PulseGuideRequest(device_id=self.id, direction=direction, duration_ms=duration_ms))

    def move_axis(self, axis: int, rate_deg_per_sec: float) -> None:
        with _translate():
            self._s.MoveAxis(pb.MoveAxisRequest(device_id=self.id, axis=axis, rate_deg_per_sec=rate_deg_per_sec))

    def park(self) -> pb.MountState:
        with _translate():
            return self._s.Park(_ref(self.id))

    def unpark(self) -> pb.MountState:
        with _translate():
            return self._s.Unpark(_ref(self.id))


# ───────────────────────── Caméra ─────────────────────────

class CameraClient:
    def __init__(self, devices: pbg.DeviceServiceStub, stub: pbg.CameraServiceStub, device_id: str) -> None:
        self._dev, self._s, self.id = devices, stub, device_id

    def connect(self) -> pb.DeviceInfo:
        with _translate():
            return self._dev.Connect(_ref(self.id))

    def disconnect(self) -> pb.DeviceInfo:
        with _translate():
            return self._dev.Disconnect(_ref(self.id))

    def info(self) -> pb.CameraInfo:
        with _translate():
            return self._s.GetInfo(_ref(self.id))

    def status(self) -> pb.CameraStatus:
        with _translate():
            return self._s.GetStatus(_ref(self.id))

    def configure(self, *, gain: Optional[int] = None, offset: Optional[int] = None,
                  bin_x: Optional[int] = None, bin_y: Optional[int] = None,
                  target_temperature_c: Optional[float] = None,
                  cooler_on: Optional[bool] = None) -> pb.CameraStatus:
        """Seuls les paramètres fournis sont modifiés."""
        req = pb.CameraSettings(device_id=self.id)
        for name, value in dict(gain=gain, offset=offset, bin_x=bin_x, bin_y=bin_y,
                                target_temperature_c=target_temperature_c, cooler_on=cooler_on).items():
            if value is not None:
                setattr(req, name, value)
        with _translate():
            return self._s.SetSettings(req)

    def expose(self, exposure_s: float, output_dir: str | Path, *, filename_stem: str = "",
               on_progress=None) -> pb.ExposureResult:
        """Bloque jusqu'à ce que le FITS soit écrit et renvoie ses métadonnées.

        on_progress(elapsed_s, total_s) est appelé pendant la pose si fourni.
        """
        req = pb.ExposeRequest(device_id=self.id, exposure_s=exposure_s,
                               output_dir=str(output_dir), filename_stem=filename_stem)
        with _translate():
            for ev in self._s.Expose(req, timeout=exposure_s + 60):
                kind = ev.WhichOneof("event")
                if kind == "progress" and on_progress:
                    on_progress(ev.progress.elapsed_s, ev.progress.total_s)
                elif kind == "result":
                    return ev.result
        raise DeviceInternalError("flux terminé sans résultat")

    def abort(self) -> None:
        with _translate():
            self._s.AbortExposure(_ref(self.id))


# ───────────────────────── Point d'entrée ─────────────────────────

class DevicesClient:
    def __init__(self, address: str = "127.0.0.1:50051", *, connect_timeout_s: float = 5.0) -> None:
        self._channel = grpc.insecure_channel(address)
        try:
            grpc.channel_ready_future(self._channel).result(timeout=connect_timeout_s)
        except grpc.FutureTimeoutError as e:
            self._channel.close()
            raise DeviceUnavailable(f"service de périphériques injoignable sur {address}") from e
        self._devices = pbg.DeviceServiceStub(self._channel)
        self._mount = pbg.MountServiceStub(self._channel)
        self._camera = pbg.CameraServiceStub(self._channel)

    def __enter__(self) -> "DevicesClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._channel.close()

    def list_devices(self, kind: int = DeviceKind.DEVICE_KIND_UNSPECIFIED, rescan: bool = False) -> list[pb.DeviceInfo]:
        with _translate():
            return list(self._devices.ListDevices(pb.ListDevicesRequest(kind=kind, rescan=rescan)).devices)

    def mount(self, device_id: str) -> MountClient:
        return MountClient(self._devices, self._mount, device_id)

    def camera(self, device_id: str) -> CameraClient:
        return CameraClient(self._devices, self._camera, device_id)
