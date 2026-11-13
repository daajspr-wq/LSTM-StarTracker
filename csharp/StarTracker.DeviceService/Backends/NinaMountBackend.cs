#if NINA
using Google.Protobuf.WellKnownTypes;
using StarTracker.Devices.V1;
// Alias plutôt que « using NINA... » : plusieurs noms existent des deux côtés (Coordinates, Epoch,
// PierSide, TrackingMode) et seraient ambigus.
using ITelescope = NINA.Equipment.Interfaces.ITelescope;
using NinaCoordinates = NINA.Astrometry.Coordinates;
using NinaEpoch = NINA.Astrometry.Epoch;
using NinaPierSide = NINA.Core.Enum.PierSide;
using NinaTrackingMode = NINA.Equipment.Interfaces.TrackingMode;
using GuideDirections = NINA.Core.Enum.GuideDirections;
using TelescopeAxes = NINA.Core.Enum.TelescopeAxes;

namespace StarTracker.DeviceService.Backends;

/// <summary>Adaptateur ITelescope (NINA) -> IMountBackend. Seul endroit qui connaît NINA.</summary>
/// <remarks>
/// NON COMPILÉ NI TESTÉ : écrit d'après les signatures lues dans ITelescope.cs.
/// Les points marqués SPIKE sont à valider avec le simulateur ASCOM (OmniSim) puis du vrai matériel.
/// </remarks>
public sealed class NinaMountBackend : IMountBackend
{
    private readonly ITelescope _t;

    public NinaMountBackend(ITelescope telescope, string backendName)
    {
        _t = telescope;
        BackendName = backendName;
    }

    public string Id => $"{BackendName}:{_t.Id}";
    public string Name => _t.Name;
    public string BackendName { get; }
    public bool Connected => _t.Connected;

    public Task<bool> ConnectAsync(CancellationToken ct) => _t.Connect(ct);
    public void Disconnect() => _t.Disconnect();

    public MountInfo GetInfo() => new()
    {
        Device = new DeviceInfo { Id = Id, Kind = DeviceKind.Mount, Name = Name, Backend = BackendName, Connected = Connected },
        CanSlew = _t.CanSlew,
        CanSync = true,                                   // SPIKE : pas de CanSync dans ITelescope, à tester
        CanPark = _t.CanPark,
        CanPulseGuide = _t.CanPulseGuide,
        CanSetTracking = _t.CanSetTrackingEnabled,
        CanSetCustomRate = _t.TrackingModes.Contains(NinaTrackingMode.Custom),
        CanMoveAxis = _t.CanMovePrimaryAxis && _t.CanMoveSecondaryAxis,
        GuideRateRaArcsecPerSec = _t.GuideRateRightAscensionArcsecPerSec,
        GuideRateDecArcsecPerSec = _t.GuideRateDeclinationArcsecPerSec,
        SiteLatitudeDeg = _t.SiteLatitude,
        SiteLongitudeDeg = _t.SiteLongitude,
    };

    public MountState GetState() => new()
    {
        DeviceId = Id,
        // SPIKE : _t.Coordinates est-il en JNOW ou J2000 selon le driver ? Vérifier _t.Coordinates.Epoch.
        Coordinates = new Devices.V1.Coordinates
        {
            RaHours = _t.RightAscension,
            DecDegrees = _t.Declination,
            Epoch = Devices.V1.Epoch.Jnow,
        },
        AltitudeDeg = _t.Altitude,
        AzimuthDeg = _t.Azimuth,
        SiderealTimeHours = _t.SiderealTime,
        SideOfPier = _t.SideOfPier switch
        {
            NinaPierSide.pierEast => Devices.V1.PierSide.East,
            NinaPierSide.pierWest => Devices.V1.PierSide.West,
            _ => Devices.V1.PierSide.Unknown,
        },
        Slewing = _t.Slewing,
        Tracking = _t.TrackingEnabled,
        TrackingMode = ToProto(_t.TrackingEnabled ? _t.TrackingMode : NinaTrackingMode.Stopped),
        Parked = _t.AtPark,
        PulseGuiding = _t.IsPulseGuiding,
        Timestamp = Timestamp.FromDateTime(DateTime.UtcNow),
    };

    public async Task<bool> SlewAsync(double raHours, double decDegrees, Devices.V1.Epoch epoch, CancellationToken ct)
        => await _t.SlewToCoordinates(ToNina(raHours, decDegrees, epoch), ct);

    public void StopSlew() => _t.StopSlew();

    public void Sync(double raHours, double decDegrees, Devices.V1.Epoch epoch)
    {
        if (!_t.Sync(ToNina(raHours, decDegrees, epoch)))
            throw new InvalidOperationException("la monture a refusé le sync");
    }

    public void SetTracking(TrackingMode mode, double raRate, double decRate)
    {
        // SPIKE : valider le comportement exact de TrackingEnabled / TrackingMode selon les drivers.
        if (mode == TrackingMode.Stopped)
        {
            _t.TrackingEnabled = false;
            return;
        }
        if (mode == TrackingMode.Custom)
        {
            _t.SetCustomTrackingRate(raRate, decRate);          // unités ASCOM, voir devices.proto
            return;
        }
        if (_t.CanSetTrackingEnabled) _t.TrackingEnabled = true;
        _t.TrackingMode = mode switch
        {
            TrackingMode.Lunar => NinaTrackingMode.Lunar,
            TrackingMode.Solar => NinaTrackingMode.Solar,
            _ => NinaTrackingMode.Sidereal,
        };
    }

    public void PulseGuide(GuideDirection direction, int durationMs)
    {
        if (!_t.CanPulseGuide) throw new NotSupportedException("pulse guide non supporté par cette monture");
        _t.PulseGuide(direction switch
        {
            GuideDirection.North => GuideDirections.guideNorth,
            GuideDirection.South => GuideDirections.guideSouth,
            GuideDirection.East => GuideDirections.guideEast,
            GuideDirection.West => GuideDirections.guideWest,
            _ => throw new ArgumentException("direction invalide"),
        }, durationMs);
        // SPIKE : PulseGuide est `void` côté NINA ; vérifier s'il rend la main avant la fin de l'impulsion.
    }

    public void MoveAxis(Axis axis, double rateDegPerSec)
        => _t.MoveAxis(axis == Axis.Primary ? TelescopeAxes.Primary : TelescopeAxes.Secondary, rateDegPerSec);

    public Task ParkAsync(CancellationToken ct) => _t.Park(ct);
    public Task UnparkAsync(CancellationToken ct) => _t.Unpark(ct);

    // ── conversions ──
    private static NinaCoordinates ToNina(double raHours, double decDegrees, Devices.V1.Epoch epoch)
        => new(raHours, decDegrees,
               epoch == Devices.V1.Epoch.J2000 ? NinaEpoch.J2000 : NinaEpoch.JNOW,
               NinaCoordinates.RAType.Hours);

    private static TrackingMode ToProto(NinaTrackingMode m) => m switch
    {
        NinaTrackingMode.Sidereal => TrackingMode.Sidereal,
        NinaTrackingMode.Lunar => TrackingMode.Lunar,
        NinaTrackingMode.Solar => TrackingMode.Solar,
        NinaTrackingMode.Custom => TrackingMode.Custom,
        NinaTrackingMode.Stopped => TrackingMode.Stopped,
        _ => TrackingMode.Unspecified,                    // "King" n'existe pas dans le contrat
    };
}
#endif
