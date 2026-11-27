"""Tests de la couche matériel (faux serveur par défaut, OmniSim via ALPACA_TEST_SERVER)."""
import threading
import time
from pathlib import Path

import pytest
from astropy.io import fits

from startracker_devices import (Axis, Coordinates, DeviceKind, DeviceNotFound, DeviceNotReady, Devices,
                                 DeviceUnavailable, Epoch, GuideDirection, InvalidParameter, NotSupported,
                                 OperationAborted, TrackingMode, convert_coordinates)


def ready(mount, mode=TrackingMode.SIDEREAL):
    mount.connect()
    mount.unpark()
    if mode is not None:
        mount.set_tracking(mode)


def dra_arcsec(a_h, b_h):
    """Écart d'AD (b - a) en secondes d'arc d'angle horaire, chemin le plus court."""
    return ((b_h - a_h + 12.0) % 24.0 - 12.0) * 15 * 3600


# ── découverte ──

def test_list_devices_and_filter(devs):
    all_ = devs.list_devices()
    assert {d.kind for d in all_} >= {DeviceKind.MOUNT, DeviceKind.CAMERA}
    mounts = devs.list_devices(DeviceKind.MOUNT)
    assert mounts and all(d.kind == DeviceKind.MOUNT for d in mounts)
    assert mounts[0].id.endswith(f"/telescope/{mounts[0].number}")


def test_unreachable_server_is_reported_not_raised():
    with Devices(servers=["127.0.0.1:1"], discover=False) as d:
        assert d.list_devices() == []
        assert "127.0.0.1:1" in d.last_errors


def test_default_local_servers_absent_is_quiet(monkeypatch):
    """Sans OmniSim ni ASCOM Remote lancés : liste vide, rapide, sans fausse alerte."""
    import startracker_devices.devices as mod
    monkeypatch.setattr(mod, "DEFAULT_LOCAL_SERVERS", ("127.0.0.1:1", "127.0.0.1:2"))
    monkeypatch.delenv("ALPACA_SERVERS", raising=False)
    t0 = time.monotonic()
    with Devices() as d:
        assert d.list_devices() == [] and d.last_errors == {}
    assert time.monotonic() - t0 < 3


def test_wrong_kind_or_bad_id(devs):
    mount_id = devs.list_devices(DeviceKind.MOUNT)[0].id
    with pytest.raises(DeviceNotFound):
        devs.camera(mount_id)
    with pytest.raises(DeviceNotFound):
        devs.mount("pas-un-identifiant")


def test_manual_id_without_listing(server):
    with Devices(servers=[], discover=False) as d:
        m = d.mount(f"{server}/telescope/0")
        m.connect()
        assert m.connected
        m.disconnect()


def test_server_down_gives_unavailable():
    with Devices(discover=False) as d:
        with pytest.raises(DeviceUnavailable):
            d.mount("127.0.0.1:1/telescope/0").connect()


# ── monture ──

def test_commands_require_connection(mount):
    with pytest.raises(DeviceNotReady):
        mount.state()


@pytest.mark.fake_only
def test_parked_mount_refuses_motion(mount):
    mount.connect()
    assert mount.state().parked
    with pytest.raises(DeviceNotReady):
        mount.pulse_guide(GuideDirection.NORTH, 100)
    with pytest.raises(DeviceNotReady):
        mount.set_tracking(TrackingMode.SIDEREAL)


def test_info_reports_capabilities(mount):
    mount.connect()
    info = mount.info()
    assert info.can_slew and info.can_pulse_guide
    assert info.native_epoch in (Epoch.JNOW, Epoch.J2000)


def test_slew_requires_tracking_then_reaches_target(mount):
    ready(mount, mode=None)
    mount.set_tracking(TrackingMode.STOPPED)
    here = mount.state().coordinates
    target = Coordinates((here.ra_hours + 0.2) % 24, max(-80.0, min(80.0, here.dec_degrees - 3.0)), here.epoch)
    with pytest.raises(DeviceNotReady):
        mount.slew_to(target.ra_hours, target.dec_degrees, target.epoch)

    mount.set_tracking(TrackingMode.SIDEREAL)
    st = mount.slew_to(target.ra_hours, target.dec_degrees, target.epoch, timeout_s=120)
    assert not st.slewing and st.tracking
    assert abs(dra_arcsec(target.ra_hours, st.coordinates.ra_hours)) < 60
    assert st.coordinates.dec_degrees == pytest.approx(target.dec_degrees, abs=60 / 3600)


def test_slew_in_j2000_is_converted(mount):
    ready(mount)
    info = mount.info()
    native = mount.state().coordinates
    goal_native = Coordinates((native.ra_hours + 0.3) % 24, max(-80.0, min(80.0, native.dec_degrees - 2.0)), info.native_epoch)
    other = Epoch.J2000 if info.native_epoch == Epoch.JNOW else Epoch.JNOW
    goal_other = convert_coordinates(goal_native, other)
    st = mount.slew_to(goal_other.ra_hours, goal_other.dec_degrees, other, timeout_s=120)
    assert abs(dra_arcsec(goal_native.ra_hours, st.coordinates.ra_hours)) < 60
    assert st.coordinates.dec_degrees == pytest.approx(goal_native.dec_degrees, abs=60 / 3600)
    back = mount.state(epoch=other).coordinates
    assert back.epoch == other and abs(dra_arcsec(goal_other.ra_hours, back.ra_hours)) < 60


def test_slew_can_be_aborted(mount):
    ready(mount)
    here = mount.state().coordinates
    far = Coordinates((here.ra_hours + 5) % 24, max(-70.0, here.dec_degrees - 60.0), here.epoch)
    threading.Timer(0.6, mount.stop_slew).start()
    with pytest.raises(OperationAborted):
        mount.slew_to(far.ra_hours, far.dec_degrees, far.epoch, timeout_s=120)
    time.sleep(0.5)
    assert not mount.state().slewing


def test_invalid_coordinates_rejected(mount):
    ready(mount)
    with pytest.raises(InvalidParameter):
        mount.slew_to(25.0, 0.0, Epoch.JNOW)
    with pytest.raises(InvalidParameter):
        mount.sync(1.0, 95.0, Epoch.JNOW)


def test_sidereal_tracking_holds_ra(mount):
    ready(mount)
    a = mount.state().coordinates.ra_hours
    time.sleep(1.5)
    b = mount.state().coordinates.ra_hours
    assert abs(dra_arcsec(a, b)) < 5


@pytest.mark.fake_only
def test_stopped_tracking_drifts_at_sidereal_rate(mount):
    ready(mount)
    mount.set_tracking(TrackingMode.STOPPED)
    a = mount.state().coordinates.ra_hours
    time.sleep(2.0)
    assert dra_arcsec(a, mount.state().coordinates.ra_hours) == pytest.approx(2.0 * 15.04, rel=0.15)


@pytest.mark.fake_only
def test_custom_rate_offsets_ra(mount):
    ready(mount)
    st = mount.set_tracking(TrackingMode.CUSTOM, ra_rate=0.5)
    assert st.tracking_mode == TrackingMode.CUSTOM
    a = mount.state().coordinates.ra_hours
    time.sleep(2.0)
    drift_s_of_ra = dra_arcsec(a, mount.state().coordinates.ra_hours) / 15
    assert drift_s_of_ra == pytest.approx(0.5 * 2.0 * 1.0027, rel=0.15)
    assert mount.set_tracking(TrackingMode.SIDEREAL).tracking_mode == TrackingMode.SIDEREAL


@pytest.mark.fake_only
def test_unsupported_tracking_rate(mount):
    ready(mount)
    with pytest.raises(NotSupported):
        mount.set_tracking(TrackingMode.KING)


def test_pulse_guide_moves_dec(mount):
    ready(mount)
    info = mount.info()
    d0 = mount.state().coordinates.dec_degrees
    mount.pulse_guide(GuideDirection.NORTH, 2000)
    moved = (mount.state().coordinates.dec_degrees - d0) * 3600
    assert moved == pytest.approx(info.guide_rate_dec_arcsec_per_s * 2.0, rel=0.25)
    with pytest.raises(InvalidParameter):
        mount.pulse_guide(GuideDirection.NORTH, 0)


def test_move_axis_validates_rate(mount):
    ready(mount)
    with pytest.raises(InvalidParameter):
        mount.move_axis(Axis.PRIMARY, 99.0)
    mount.move_axis(Axis.SECONDARY, 0.5)
    time.sleep(0.3)
    mount.move_axis(Axis.SECONDARY, 0.0)


def test_state_stream(mount):
    ready(mount)
    stamps = []
    for st in mount.stream_state(interval_s=0.1):
        stamps.append(st.timestamp)
        if len(stamps) == 4:
            break
    assert stamps == sorted(stamps)


def test_sync_and_park(mount):
    ready(mount)
    here = mount.state().coordinates
    goal = Coordinates((here.ra_hours + 0.01) % 24, here.dec_degrees - 0.1, here.epoch)
    mount.sync(goal.ra_hours, goal.dec_degrees, goal.epoch)
    c = mount.state().coordinates
    assert abs(dra_arcsec(goal.ra_hours, c.ra_hours)) < 30
    assert c.dec_degrees == pytest.approx(goal.dec_degrees, abs=30 / 3600)
    st = mount.park(timeout_s=180)
    assert st.parked and not st.tracking
    assert not mount.unpark().parked


# ── caméra ──

def test_camera_info_and_settings(camera):
    camera.connect()
    info = camera.info()
    assert info.width_px > 0 and info.height_px > 0
    if info.can_set_gain:
        with pytest.raises(InvalidParameter):
            camera.configure(gain=info.gain_max + 1)
        assert camera.configure(gain=info.gain_min).gain == info.gain_min
    if info.max_bin_x >= 2:
        st = camera.configure(bin_x=2)
        assert (st.bin_x, st.bin_y) == (2, 2)
        camera.configure(bin_x=1)


def test_expose_writes_fits(camera, tmp_path):
    camera.connect()
    info = camera.info()
    seen = []
    res = camera.expose(max(0.5, info.exposure_min_s), tmp_path, "t1",
                        on_progress=lambda e, t: seen.append(e), extra_header={"OBJECT": "test"})
    assert res.fits_path == (tmp_path / "t1.fits").resolve()
    assert seen, "on_progress n'a jamais été appelé"
    with fits.open(res.fits_path) as hdul:
        data, hdr = hdul[0].data, hdul[0].header
        assert data.shape == (res.height_px, res.width_px) == (info.height_px, info.width_px)
        assert hdr["EXPTIME"] == pytest.approx(res.exposure_s, rel=0.01)
        assert hdr["OBJECT"] == "test" and "DATE-OBS" in hdr


def test_binned_exposure_has_binned_shape(camera, tmp_path):
    camera.connect()
    info = camera.info()
    if info.max_bin_x < 2:
        pytest.skip("caméra sans binning")
    camera.configure(bin_x=2)
    res = camera.expose(0.5, tmp_path, "bin2")
    assert (res.width_px, res.height_px) == (info.width_px // 2, info.height_px // 2)
    camera.configure(bin_x=1)


def test_expose_rejects_bad_args(camera, tmp_path):
    camera.connect()
    with pytest.raises(InvalidParameter):
        camera.expose(0.5, tmp_path / "absent")
    with pytest.raises(InvalidParameter):
        camera.expose(-1, tmp_path)


def test_expose_can_be_aborted(camera, tmp_path):
    camera.connect()
    threading.Timer(0.6, camera.abort).start()
    with pytest.raises(OperationAborted):
        camera.expose(20, tmp_path)
    assert not list(tmp_path.glob("*.fits"))
    time.sleep(0.3)
    assert camera.status().state == "idle"


@pytest.mark.fake_only
def test_json_image_fallback(tmp_path):
    """Serveur sans ImageBytes : l'image arrive en JSON et doit avoir la même forme."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from fake_alpaca import FakeAlpacaServer
    srv = FakeAlpacaServer(port=0, imagebytes=False).start()
    try:
        with Devices(servers=[srv.address], discover=False) as d:
            cam = d.camera(f"{srv.address}/camera/0")
            cam.connect()
            cam.configure(bin_x=4)            # petite image : le JSON est lent
            res = cam.expose(0.2, tmp_path, "json")
            with fits.open(res.fits_path) as hdul:
                assert hdul[0].data.shape == (240, 320)
    finally:
        srv.stop()
