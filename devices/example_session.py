#!/usr/bin/env python3
"""Session complète vue de l'application : découverte, pointage, guidage, pose.

Sous Windows avec OmniSim lancé :      python example_session.py
Sans Windows (faux serveur de test) :   python tests/fake_alpaca.py   puis, dans un autre terminal,
                                        python example_session.py
Autre serveur :                         python example_session.py 192.168.1.20:11111
"""
import sys
import tempfile
from itertools import islice

from startracker_devices import (Devices, DeviceKind, DeviceNotReady, Epoch, GuideDirection,
                                 InvalidParameter, TrackingMode)

servers = sys.argv[1:] or None          # None = OmniSim et ASCOM Remote sur ce PC

with Devices(servers=servers) as devs:
    found = devs.list_devices()
    print("Appareils :", [(d.id, d.name) for d in found])
    if devs.last_errors:
        print("Serveurs en erreur :", devs.last_errors)
    mounts, cameras = devs.list_devices(DeviceKind.MOUNT), devs.list_devices(DeviceKind.CAMERA)
    if not mounts or not cameras:
        sys.exit("Il faut une monture et une caméra : OmniSim est-il lancé ?")

    # ── monture ──
    mount = devs.mount(mounts[0].id)
    try:
        mount.state()                                   # avant connexion : erreur explicite
    except DeviceNotReady as e:
        print("Attendu, non connectée :", e.detail)

    mount.connect()
    info = mount.info()
    print(f"Repère de la monture : {info.native_epoch.value}, pulse guide : {info.can_pulse_guide}")
    mount.unpark()
    mount.set_tracking(TrackingMode.SIDEREAL)

    # M81 (catalogue, donc J2000), toujours au-dessus de l'horizon en France.
    # La conversion vers le repère de la monture (souvent JNow) est automatique.
    st = mount.slew_to(9.9259, 69.0653, Epoch.J2000)
    print(f"Arrivée : AD={st.coordinates.ra_hours:.4f} h  Dec={st.coordinates.dec_degrees:.3f}° "
          f"({st.coordinates.epoch.value}), alt={st.altitude_deg:.1f}°")

    for s in islice(mount.stream_state(interval_s=0.3, epoch=Epoch.J2000), 3):
        print(f"  flux J2000 : AD={s.coordinates.ra_hours:.5f} h  Dec={s.coordinates.dec_degrees:.4f}°")

    mount.pulse_guide(GuideDirection.NORTH, 300)        # une correction de guidage
    try:
        mount.slew_to(25, 0, Epoch.J2000)               # AD impossible
    except InvalidParameter as e:
        print("Attendu, paramètre invalide :", e.detail)

    # ── caméra ──
    cam = devs.camera(cameras[0].id)
    cam.connect()
    cinfo = cam.info()
    print(f"Caméra {cinfo.sensor_name or cinfo.device.name} : {cinfo.width_px}x{cinfo.height_px} px")
    if cinfo.can_set_gain:
        cam.configure(gain=cinfo.gain_min)
    with tempfile.TemporaryDirectory() as tmp:
        res = cam.expose(1.0, tmp, on_progress=lambda e, t: print(f"  pose {e:.1f}/{t:.1f} s"),
                         extra_header={"OBJECT": "M81"})
        print(f"FITS écrit : {res.fits_path.name} ({res.width_px}x{res.height_px})")

    mount.park()
    cam.disconnect()
    mount.disconnect()
