# startracker-devices

Couche « matériel » de l'application : un **contrat gRPC** entre le backend Python et un **service C#**
qui pilote caméras et montures via `NINA.Equipment` (ASCOM, Alpaca, SDK constructeurs).

```
Frontend ── Backend Python ──(gRPC, localhost)── Service C# ── NINA.Equipment ── monture / caméra
                │
                └── en développement : sim_server.py (simulateur) à la place du service C#
```

## Contenu

| Dossier | Rôle | État |
|---|---|---|
| `proto/startracker/devices/v1/devices.proto` | Le contrat (découverte, monture, caméra, codes d'erreur) | Compile, utilisé par les tests |
| `python/sim_server.py` | Simulateur qui implémente le contrat | **Testé** (16 tests) |
| `python/devices_client.py` | Client Python typé, erreurs gRPC → exceptions | **Testé** (via `example_session.py`) |
| `python/test_sim.py` | Tests de contrat (réutilisables contre le service C#) | **Passent** |
| `csharp/StarTracker.DeviceService` | Squelette du service C# (monture uniquement) | **Jamais compilé** (voir plus bas) |

## Démarrage rapide (Python, sans matériel)

```bash
cd python
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python generate.py            # génère python/generated/ à partir du .proto (à refaire si le .proto change)
pytest -q                     # 16 tests contre le simulateur

python sim_server.py          # terminal 1
python example_session.py     # terminal 2 : session complète (slew, flux d'état, pulse guide, pose FITS)
```

Le reste du backend n'importe que `devices_client.py` : il ne voit jamais gRPC, ASCOM ni NINA.

## Décisions de conception

- **Pas d'image dans gRPC** : `Expose` écrit un FITS sur disque et renvoie son chemin + métadonnées.
- **Erreurs = codes gRPC** (liste en tête du `.proto`), traduits en exceptions Python par le client
  (`DeviceNotReady`, `NotSupported`, `OperationAborted`, ...). Pas de booléen « ok ».
- **Capacités explicites** (`MountInfo.can_*`) : le client vérifie avant d'appeler une méthode optionnelle.
- **Unités du suivi personnalisé = unités ASCOM** (`ra_rate` : s d'AD par s sidérale, `dec_rate` : "/s).
  Vérifié : l'adaptateur ASCOM de NINA les transmet tels quels à `Telescope.RightAscensionRate/DeclinationRate`.
- **Le simulateur est une spécification vivante** : si le service C# se comporte autrement qu'un test
  de `test_sim.py`, c'est le service C# qui a tort. Les tests peuvent viser le service C# avec
  `DEVICE_SERVICE_ADDR=127.0.0.1:50051 pytest -q`.

## Service C# : ce qui est vérifié et ce qui ne l'est pas

Le code C# a été écrit à partir des signatures lues dans le dépôt NINA (`ITelescope`, `IDevice`,
`ASCOMInteraction`, `AlpacaInteraction`, `TelescopeChooserVM`), **sans pouvoir le compiler** : il n'y avait
pas de .NET dans l'environnement où il a été écrit. Attends-toi à quelques erreurs de compilation.
Les points à valider sont marqués `SPIKE` dans le code.

### Premier test (spike) à faire avant d'écrire autre chose

1. Installer le SDK .NET demandé par NINA (voir `global.json` du dépôt NINA) et cloner NINA.
2. Installer la plateforme ASCOM et le simulateur **OmniSim** (ASCOM Alpaca Simulators).
3. `dotnet build -p:NinaRoot=C:\src\nina` dans `csharp/StarTracker.DeviceService`.
4. Obtenir un `IProfileService` dans `BackendDiscovery.CreateProfileService()` (`SPIKE`). C'est le point le plus
   incertain : `ASCOMInteraction` et `AlpacaInteraction` en ont besoin, et leur instanciation n'a pas été essayée.
5. Lancer le service, puis `DEVICE_SERVICE_ADDR=127.0.0.1:50051 pytest -q -k mount` : les tests de monture
   disent ce qui diffère du comportement attendu.
6. Vérifier que `NINA.Equipment` se charge sans contexte WPF (le projet active WPF et une trentaine de
   fichiers touchent à `System.Windows`/`Dispatcher`). Si ça bloque, le plan B est de n'utiliser NINA que pour
   les caméras à SDK natif et de parler Alpaca directement depuis Python (`alpyca`) pour le reste.

### À prévoir ensuite

- `CameraGrpcService` + `NinaCameraBackend` : même schéma que la monture (`ICamera` : `StartExposure`,
  `DownloadExposure`, gain, binning, refroidissement). Découverte : `ASCOMInteraction.GetCameras(...)`,
  `AlpacaInteraction.GetCameras(...)` et les fournisseurs SDK (ASI, QHY, ...) de `NINA.Equipment/SDK`.
- Thread **STA** pour les drivers ASCOM COM classiques.
- Épingler un commit de NINA (sous-module git). Les fichiers NINA modifiés restent en MPL 2.0 ;
  ce dossier ne contient aucun fichier NINA.
- Licences des SDK constructeurs à vérifier avant distribution.

## Pour le modèle de suivi (LSTM)

Le contrat expose déjà ce qu'il faut pour enregistrer des séries temporelles : `StreamState` (position,
horodatée côté service), `PulseGuide`, `SetTracking` en mode `CUSTOM`, et `Expose` (FITS horodaté). Le simulateur
ne modélise pas encore d'erreur périodique de la monture ; si tu veux entraîner ou tester le modèle hors
matériel, c'est l'extension la plus utile à y ajouter.
