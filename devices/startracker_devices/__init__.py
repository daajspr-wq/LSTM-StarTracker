"""startracker_devices : piloter montures et caméras via ASCOM Alpaca.

    from startracker_devices import Devices, DeviceKind, Epoch, TrackingMode

    with Devices(servers=["127.0.0.1:32323"]) as devs:
        mount = devs.mount(devs.list_devices(DeviceKind.MOUNT)[0].id)
        mount.connect()
        mount.unpark()
        mount.set_tracking(TrackingMode.SIDEREAL)
        mount.slew_to(5.588, -5.39, Epoch.J2000)      # M42
"""
from .coords import convert as convert_coordinates
from .devices import Camera, Devices, Mount
from .errors import (DeviceError, DeviceInternalError, DeviceNotFound, DeviceNotReady, DeviceTimeout,
                     DeviceUnavailable, InvalidParameter, NotSupported, OperationAborted)
from .types import (Axis, CameraInfo, CameraStatus, Coordinates, DeviceInfo, DeviceKind, Epoch,
                    ExposureResult, GuideDirection, MountInfo, MountState, PierSide, TrackingMode)

__all__ = [
    "Devices", "Mount", "Camera", "convert_coordinates",
    "Axis", "CameraInfo", "CameraStatus", "Coordinates", "DeviceInfo", "DeviceKind", "Epoch",
    "ExposureResult", "GuideDirection", "MountInfo", "MountState", "PierSide", "TrackingMode",
    "DeviceError", "DeviceInternalError", "DeviceNotFound", "DeviceNotReady", "DeviceTimeout",
    "DeviceUnavailable", "InvalidParameter", "NotSupported", "OperationAborted",
]
