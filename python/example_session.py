#!/usr/bin/env python3
"""Session complète vue du backend : c'est ce que devra faire l'application.

Terminal 1 :  python sim_server.py
Terminal 2 :  python example_session.py
"""
import os
import tempfile
from itertools import islice

from devices_client import (Axis, DevicesClient, DeviceNotReady, GuideDirection,
                            InvalidParameter, TrackingMode)

with DevicesClient(os.environ.get("DEVICE_SERVICE_ADDR", "127.0.0.1:50051")) as client:
    print("Appareils :", [(d.id, d.name, d.backend) for d in client.list_devices()])

    # ── monture ──
    mount = client.mount("sim-mount")

    try:
        mount.state()                               # avant connexion : erreur explicite
    except DeviceNotReady as e:
        print("Attendu, non connecté :", e.detail)

    mount.connect()
    info = mount.info()
    print(f"Capacités : pulse_guide={info.can_pulse_guide} custom_rate={info.can_set_custom_rate}")
    mount.unpark()
    mount.set_tracking(TrackingMode.TRACKING_MODE_SIDEREAL)

    here = mount.state().coordinates
    print(f"Pointage actuel : RA={here.ra_hours:.4f} h  Dec={here.dec_degrees:.3f}°")

    final = mount.slew_to((here.ra_hours + 0.3) % 24, here.dec_degrees - 4.0)
    print(f"Slew terminé : RA={final.coordinates.ra_hours:.4f} h  tracking={final.tracking}")

    for st in islice(mount.stream_state(interval_ms=200), 3):
        print(f"  flux : RA={st.coordinates.ra_hours:.5f} alt={st.altitude_deg:.1f}° pier={st.side_of_pier}")

    # Boucle de guidage minimale : une correction de 300 ms vers le nord
    mount.pulse_guide(GuideDirection.GUIDE_DIRECTION_NORTH, 300)

    try:
        mount.move_axis(Axis.AXIS_PRIMARY, 99.0)    # vitesse absurde
    except InvalidParameter as e:
        print("Attendu, paramètre invalide :", e.detail)

    # ── caméra ──
    cam = client.camera("sim-camera")
    cam.connect()
    cam.configure(gain=120, bin_x=1, bin_y=1, target_temperature_c=-10, cooler_on=True)
    with tempfile.TemporaryDirectory() as tmp:
        res = cam.expose(1.0, tmp, on_progress=lambda e, t: print(f"  pose {e:.1f}/{t:.1f} s"))
        print(f"FITS écrit : {res.fits_path} ({res.width_px}x{res.height_px}, {res.bit_depth} bit)")

    mount.park()
    cam.disconnect()
    mount.disconnect()
