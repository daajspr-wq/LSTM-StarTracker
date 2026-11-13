"""Tests de contrat contre le simulateur.

Lancer :  python generate.py && pytest -q

Ces mêmes tests devront passer contre le service C# (en pointant DEVICE_SERVICE_ADDR
vers lui) : ils décrivent le comportement attendu du contrat, pas celui du simulateur.
"""
import os
import sys
import threading
import time
from pathlib import Path

import grpc
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).parent / "generated"))

from startracker.devices.v1 import devices_pb2 as pb  # noqa: E402
from startracker.devices.v1 import devices_pb2_grpc as pbg  # noqa: E402

import sim_server  # noqa: E402

MOUNT, CAMERA = "sim-mount", "sim-camera"


@pytest.fixture()
def stubs():
    """Un serveur neuf par test (état isolé), ou le serveur désigné par DEVICE_SERVICE_ADDR."""
    addr = os.environ.get("DEVICE_SERVICE_ADDR")
    server = None
    if addr is None:
        server, port = sim_server.build_server(port=0)
        server.start()
        addr = f"127.0.0.1:{port}"
    channel = grpc.insecure_channel(addr)
    grpc.channel_ready_future(channel).result(timeout=5)
    yield (pbg.DeviceServiceStub(channel), pbg.MountServiceStub(channel), pbg.CameraServiceStub(channel))
    channel.close()
    if server:
        server.stop(grace=0)


def code_of(exc: pytest.ExceptionInfo) -> grpc.StatusCode:
    return exc.value.code()


def ref(device_id):
    return pb.DeviceRef(device_id=device_id)


def ready_mount(dev, mount, mode=pb.TRACKING_MODE_SIDEREAL):
    dev.Connect(ref(MOUNT))
    mount.Unpark(ref(MOUNT))
    if mode is not None:
        mount.SetTracking(pb.SetTrackingRequest(device_id=MOUNT, mode=mode))


# ── découverte / connexion ──

def test_list_devices_and_filter(stubs):
    dev, _, _ = stubs
    assert {d.id for d in dev.ListDevices(pb.ListDevicesRequest()).devices} == {MOUNT, CAMERA}
    only_cams = dev.ListDevices(pb.ListDevicesRequest(kind=pb.DEVICE_KIND_CAMERA)).devices
    assert [d.id for d in only_cams] == [CAMERA]


def test_unknown_device_is_not_found(stubs):
    dev, mount, _ = stubs
    with pytest.raises(grpc.RpcError) as e:
        dev.Connect(ref("nope"))
    assert code_of(e) == grpc.StatusCode.NOT_FOUND
    with pytest.raises(grpc.RpcError) as e:
        mount.GetState(ref(CAMERA))           # mauvais type d'appareil
    assert code_of(e) == grpc.StatusCode.NOT_FOUND


def test_commands_require_connection(stubs):
    _, mount, cam = stubs
    with pytest.raises(grpc.RpcError) as e:
        mount.GetState(ref(MOUNT))
    assert code_of(e) == grpc.StatusCode.FAILED_PRECONDITION
    with pytest.raises(grpc.RpcError) as e:
        cam.GetStatus(ref(CAMERA))
    assert code_of(e) == grpc.StatusCode.FAILED_PRECONDITION


# ── monture ──

def test_parked_mount_refuses_motion(stubs):
    dev, mount, _ = stubs
    dev.Connect(ref(MOUNT))
    assert mount.GetState(ref(MOUNT)).parked
    with pytest.raises(grpc.RpcError) as e:
        mount.PulseGuide(pb.PulseGuideRequest(device_id=MOUNT, direction=pb.GUIDE_DIRECTION_NORTH, duration_ms=100))
    assert code_of(e) == grpc.StatusCode.FAILED_PRECONDITION


def test_slew_requires_tracking_then_reaches_target(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount, mode=None)
    here = mount.GetState(ref(MOUNT)).coordinates
    target = pb.Coordinates(ra_hours=(here.ra_hours + 0.2) % 24, dec_degrees=here.dec_degrees - 3.0, epoch=pb.EPOCH_JNOW)
    with pytest.raises(grpc.RpcError) as e:
        mount.SlewToCoordinates(pb.SlewRequest(device_id=MOUNT, target=target))
    assert code_of(e) == grpc.StatusCode.FAILED_PRECONDITION

    mount.SetTracking(pb.SetTrackingRequest(device_id=MOUNT, mode=pb.TRACKING_MODE_SIDEREAL))
    res = mount.SlewToCoordinates(pb.SlewRequest(device_id=MOUNT, target=target), timeout=15)
    assert res.completed and not res.final_state.slewing and res.final_state.tracking
    assert res.final_state.coordinates.ra_hours == pytest.approx(target.ra_hours, abs=1e-3)
    assert res.final_state.coordinates.dec_degrees == pytest.approx(target.dec_degrees, abs=1e-3)


def test_slew_can_be_aborted(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount)
    here = mount.GetState(ref(MOUNT)).coordinates
    far = pb.Coordinates(ra_hours=(here.ra_hours + 6) % 24, dec_degrees=max(-80.0, here.dec_degrees - 60.0), epoch=pb.EPOCH_JNOW)
    threading.Timer(0.3, lambda: mount.StopSlew(ref(MOUNT))).start()
    with pytest.raises(grpc.RpcError) as e:
        mount.SlewToCoordinates(pb.SlewRequest(device_id=MOUNT, target=far), timeout=20)
    assert code_of(e) == grpc.StatusCode.ABORTED
    assert not mount.GetState(ref(MOUNT)).slewing


def test_invalid_coordinates_rejected(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount)
    with pytest.raises(grpc.RpcError) as e:
        mount.SlewToCoordinates(pb.SlewRequest(device_id=MOUNT, target=pb.Coordinates(ra_hours=25, dec_degrees=0)))
    assert code_of(e) == grpc.StatusCode.INVALID_ARGUMENT


def test_sidereal_tracking_holds_ra_and_stopped_drifts(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount, mode=pb.TRACKING_MODE_SIDEREAL)
    a = mount.GetState(ref(MOUNT)).coordinates.ra_hours
    time.sleep(1.0)
    b = mount.GetState(ref(MOUNT)).coordinates.ra_hours
    assert b == pytest.approx(a, abs=1e-6)

    mount.SetTracking(pb.SetTrackingRequest(device_id=MOUNT, mode=pb.TRACKING_MODE_STOPPED))
    a = mount.GetState(ref(MOUNT)).coordinates.ra_hours
    time.sleep(2.0)
    drift_arcsec = (mount.GetState(ref(MOUNT)).coordinates.ra_hours - a) * 15 * 3600
    assert drift_arcsec == pytest.approx(2.0 * 15.04, rel=0.1)     # ≈ vitesse sidérale


def test_custom_tracking_rate_offsets_ra(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount, mode=None)
    mount.SetTracking(pb.SetTrackingRequest(device_id=MOUNT, mode=pb.TRACKING_MODE_CUSTOM, ra_rate=0.5, dec_rate=0.0))
    a = mount.GetState(ref(MOUNT)).coordinates.ra_hours
    time.sleep(2.0)
    drift_s = (mount.GetState(ref(MOUNT)).coordinates.ra_hours - a) * 3600     # secondes d'AD
    assert drift_s == pytest.approx(0.5 * 2.0 * 1.0027, rel=0.1)


def test_pulse_guide_moves_by_guide_rate(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount)
    info = mount.GetInfo(ref(MOUNT))
    d0 = mount.GetState(ref(MOUNT)).coordinates.dec_degrees
    mount.PulseGuide(pb.PulseGuideRequest(device_id=MOUNT, direction=pb.GUIDE_DIRECTION_NORTH, duration_ms=2000))
    moved = (mount.GetState(ref(MOUNT)).coordinates.dec_degrees - d0) * 3600
    assert moved == pytest.approx(info.guide_rate_dec_arcsec_per_sec * 2.0, rel=0.05)
    with pytest.raises(grpc.RpcError) as e:
        mount.PulseGuide(pb.PulseGuideRequest(device_id=MOUNT, direction=pb.GUIDE_DIRECTION_NORTH, duration_ms=0))
    assert code_of(e) == grpc.StatusCode.INVALID_ARGUMENT


def test_state_stream_yields_periodically(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount)
    stream = mount.StreamState(pb.StreamStateRequest(device_id=MOUNT, interval_ms=100))
    stamps = []
    for st in stream:
        stamps.append(st.timestamp.ToNanoseconds())
        if len(stamps) == 5:
            stream.cancel()
            break
    assert len(stamps) == 5 and stamps == sorted(stamps)


def test_sync_and_park(stubs):
    dev, mount, _ = stubs
    ready_mount(dev, mount)
    mount.Sync(pb.SyncRequest(device_id=MOUNT, coordinates=pb.Coordinates(ra_hours=5.5, dec_degrees=-12.0)))
    c = mount.GetState(ref(MOUNT)).coordinates
    assert c.ra_hours == pytest.approx(5.5, abs=1e-3) and c.dec_degrees == pytest.approx(-12.0, abs=1e-3)
    st = mount.Park(ref(MOUNT))
    assert st.parked and not st.tracking


# ── caméra ──

def test_camera_settings_validation_and_cooling(stubs):
    dev, _, cam = stubs
    dev.Connect(ref(CAMERA))
    info = cam.GetInfo(ref(CAMERA))
    with pytest.raises(grpc.RpcError) as e:
        cam.SetSettings(pb.CameraSettings(device_id=CAMERA, gain=info.gain_max + 1))
    assert code_of(e) == grpc.StatusCode.INVALID_ARGUMENT
    st = cam.SetSettings(pb.CameraSettings(device_id=CAMERA, gain=200, bin_x=2, bin_y=2, cooler_on=True, target_temperature_c=-10))
    assert (st.gain, st.bin_x, st.bin_y, st.cooler_on) == (200, 2, 2, True)


def test_expose_streams_progress_then_writes_fits(stubs, tmp_path):
    dev, _, cam = stubs
    dev.Connect(ref(CAMERA))
    cam.SetSettings(pb.CameraSettings(device_id=CAMERA, gain=150))
    events = list(cam.Expose(pb.ExposeRequest(device_id=CAMERA, exposure_s=0.6, output_dir=str(tmp_path), filename_stem="t1"), timeout=15))
    kinds = [e.WhichOneof("event") for e in events]
    assert kinds[-1] == "result" and kinds.count("result") == 1 and "progress" in kinds
    res = events[-1].result
    assert Path(res.fits_path) == (tmp_path / "t1.fits").resolve()
    with fits.open(res.fits_path) as hdul:
        assert hdul[0].data.shape == (res.height_px, res.width_px)
        assert hdul[0].header["EXPTIME"] == pytest.approx(0.6)
        assert hdul[0].header["GAIN"] == 150


def test_expose_bad_output_dir_and_bad_duration(stubs, tmp_path):
    dev, _, cam = stubs
    dev.Connect(ref(CAMERA))
    with pytest.raises(grpc.RpcError) as e:
        list(cam.Expose(pb.ExposeRequest(device_id=CAMERA, exposure_s=0.1, output_dir=str(tmp_path / "absent"))))
    assert code_of(e) == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError) as e:
        list(cam.Expose(pb.ExposeRequest(device_id=CAMERA, exposure_s=0.0, output_dir=str(tmp_path))))
    assert code_of(e) == grpc.StatusCode.INVALID_ARGUMENT


def test_expose_can_be_aborted(stubs, tmp_path):
    dev, _, cam = stubs
    dev.Connect(ref(CAMERA))
    threading.Timer(0.5, lambda: cam.AbortExposure(ref(CAMERA))).start()
    with pytest.raises(grpc.RpcError) as e:
        list(cam.Expose(pb.ExposeRequest(device_id=CAMERA, exposure_s=30, output_dir=str(tmp_path)), timeout=15))
    assert code_of(e) == grpc.StatusCode.ABORTED
    assert not list(tmp_path.glob("*.fits"))
    assert not cam.GetStatus(ref(CAMERA)).exposing
