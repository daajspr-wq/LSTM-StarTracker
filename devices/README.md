# startracker-devices

Couche « matériel » du projet : piloter la monture et la caméra depuis Python, via **ASCOM Alpaca**.
Tout est en Python, sans programme compilé de notre côté, donc rien à signer.

```
Application (Python) ──HTTP──► ASCOM Remote ──► driver ASCOM ──► monture / caméra   (vrai matériel)
                       └─HTTP──► OmniSim (monture et caméra simulées)                (développement)
```

ASCOM Remote et OmniSim sont tous les deux installés avec la **plateforme ASCOM 7**.

## Installation (Windows)

1. Installer la **plateforme ASCOM 7.1 Update 3** depuis ascom-standards.org (rubrique Downloads).
   OmniSim et ASCOM Remote sont inclus.
2. Dans ce dossier, créer l'environnement Python (Python 3.10 ou plus) :

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```

   L'option `-e` installe le paquet en mode développement : `import startracker_devices`
   fonctionne alors depuis n'importe quel dossier du projet, et les modifications sont prises en compte
   sans réinstaller.

3. Vérifier :

   ```powershell
   pytest -q                     # 27 tests contre le faux serveur intégré, sans rien lancer d'autre
   ```

### Si Windows bloque numpy ou astropy (Smart App Control)

Le message ressemble à « Une stratégie de contrôle d'application a bloqué ce fichier ». Dans ce cas,
faire tourner la partie Python dans **WSL** (Ubuntu) au lieu de Windows. OmniSim et ASCOM Remote restent
sous Windows. Pour que WSL les atteigne à l'adresse `127.0.0.1`, activer le réseau « mirrored » de WSL
(fichier `.wslconfig`, Windows 11 22H2 ou plus récent), sinon utiliser l'adresse IP du PC Windows et
autoriser les connexions distantes dans OmniSim.

## Développer avec OmniSim

1. Lancer OmniSim (dans le menu Démarrer, cherche « OmniSim »). Il écoute sur le port **32323**.
2. Lancer l'exemple, qui fait une session complète (pointage, flux d'état, guidage, pose FITS) :

   ```powershell
   python example_session.py
   ```

3. Lancer les tests contre OmniSim :

   ```powershell
   $env:ALPACA_TEST_SERVER="127.0.0.1:32323"; pytest -q
   ```

   Les tests marqués `fake_only` dépendent du faux serveur et sont alors sautés.

Sans Windows (Linux, WSL, Mac), le faux serveur peut remplacer OmniSim :
`python tests/fake_alpaca.py --port 32323`, puis `python example_session.py` dans un autre terminal.
Ce faux serveur sert aux tests : la référence reste OmniSim.

## Avec le vrai matériel

1. Installer le driver ASCOM de la monture et celui de la caméra (fournis par les fabricants).
2. Ouvrir **ASCOM Remote**, y ajouter la monture et la caméra, et démarrer le serveur (port **11111**
   par défaut).
3. L'application les trouve sans réglage : `Devices()` interroge OmniSim et ASCOM Remote sur ce PC.

Une monture ou une caméra nativement Alpaca (sur le réseau) se pilote directement :
`Devices(servers=["192.168.1.20:11111"])`, ou `Devices(discover=True)` pour la chercher sur le réseau local.

## Utilisation

```python
from startracker_devices import Devices, DeviceKind, Epoch, GuideDirection, TrackingMode

with Devices() as devs:                                   # OmniSim + ASCOM Remote sur ce PC
    mount = devs.mount(devs.list_devices(DeviceKind.MOUNT)[0].id)
    mount.connect()
    mount.unpark()
    mount.set_tracking(TrackingMode.SIDEREAL)
    mount.slew_to(9.9259, 69.0653, Epoch.J2000)            # M81, coordonnées de catalogue
    mount.pulse_guide(GuideDirection.NORTH, 300)            # correction de guidage, en ms

    cam = devs.camera(devs.list_devices(DeviceKind.CAMERA)[0].id)
    cam.connect()
    cam.configure(gain=100, bin_x=2)
    result = cam.expose(5.0, "images/", extra_header={"OBJECT": "M81"})
    print(result.fits_path)
```

| Monture | Rôle |
|---|---|
| `info()` | Capacités (`can_slew`, `can_pulse_guide`...), vitesses de guidage, repère natif |
| `state(epoch=None)` | Position, alt/az, côté du pilier, suivi, parcage... |
| `stream_state(interval_s, epoch)` | Itérateur d'états, pour l'interface ou l'enregistrement de données |
| `slew_to(ra_h, dec_deg, epoch)` | Pointage bloquant ; `stop_slew()` l'interrompt depuis un autre thread |
| `sync(ra_h, dec_deg, epoch)` | Recale la monture (après un plate solving) |
| `set_tracking(mode, ra_rate, dec_rate)` | Arrêt, sidéral, lunaire, solaire, ou personnalisé |
| `pulse_guide(direction, ms)` | Impulsion de guidage |
| `move_axis(axis, deg_s)` | Rotation continue d'un axe (0 = arrêt) |
| `park()` / `unpark()` | |

| Caméra | Rôle |
|---|---|
| `info()` / `status()` | Capteur, limites, température, état |
| `configure(gain, offset, bin_x, bin_y, target_temperature_c, cooler_on)` | Ne change que ce qui est fourni |
| `expose(s, dossier, nom, on_progress, extra_header)` | Pose bloquante, écrit un FITS ; `abort()` l'interrompt |

### Erreurs

Toutes les erreurs héritent de `DeviceError` : `DeviceNotReady` (non connecté, parqué, suivi arrêté),
`NotSupported` (l'appareil ne sait pas faire), `InvalidParameter`, `OperationAborted`, `DeviceTimeout`,
`DeviceUnavailable` (serveur injoignable), `DeviceNotFound`, `DeviceInternalError`.
`devs.last_errors` donne les serveurs en erreur, utile pour un écran de diagnostic.

### Repères de coordonnées

Les catalogues donnent du **J2000**, la plupart des montures travaillent en **JNow** (écart supérieur à
0,3° aujourd'hui). `slew_to` et `sync` exigent donc le repère (`Epoch`) et convertissent si besoin.
`state()` renvoie le repère de la monture, ou celui demandé avec `state(epoch=Epoch.J2000)`.

### Unités du suivi personnalisé

Unités ASCOM, en décalage par rapport au sidéral : `ra_rate` en secondes d'AD par seconde sidérale,
`dec_rate` en secondes d'arc par seconde.

## État

- Testé contre le faux serveur de `tests/` uniquement. **Pas encore testé contre OmniSim ni du vrai
  matériel** : c'est la première chose à faire sous Windows.
- Caméras sans driver ASCOM : non gérées.
- Le téléchargement de l'image (fait par alpyca) n'a pas de délai d'expiration : une coupure réseau
  pendant le transfert bloquerait `expose`.
