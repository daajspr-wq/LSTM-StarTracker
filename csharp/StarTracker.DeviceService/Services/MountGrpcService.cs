using Google.Protobuf.WellKnownTypes;
using Grpc.Core;
using StarTracker.Devices.V1;
using static StarTracker.DeviceService.Services.ErrorMapping;

namespace StarTracker.DeviceService.Services;

/// <summary>
/// Traduit le contrat gRPC vers IMountBackend. Les validations et les erreurs doivent se comporter
/// comme le simulateur Python : python/test_sim.py est la référence (voir DEVICE_SERVICE_ADDR).
/// </summary>
public sealed class MountGrpcService(DeviceRegistry registry) : Devices.V1.MountService.MountServiceBase
{
    public override Task<MountInfo> GetInfo(DeviceRef r, ServerCallContext c)
        => Task.FromResult(Guard(() => registry.Mount(r.DeviceId).GetInfo()));

    public override Task<MountState> GetState(DeviceRef r, ServerCallContext c)
        => Task.FromResult(Guard(() => registry.Mount(r.DeviceId).GetState()));

    public override async Task StreamState(StreamStateRequest r, IServerStreamWriter<MountState> stream, ServerCallContext c)
    {
        var m = Guard(() => registry.Mount(r.DeviceId));
        var period = TimeSpan.FromMilliseconds(r.IntervalMs == 0 ? 500 : r.IntervalMs);
        try
        {
            while (!c.CancellationToken.IsCancellationRequested && m.Connected)
            {
                await stream.WriteAsync(Guard(m.GetState), c.CancellationToken);
                await Task.Delay(period, c.CancellationToken);
            }
        }
        catch (OperationCanceledException) { /* le client a fermé le flux : normal */ }
    }

    public override async Task<SlewResult> SlewToCoordinates(SlewRequest r, ServerCallContext c)
        => await GuardAsync(async () =>
        {
            var t = r.Target;
            if (t.RaHours is < 0 or >= 24 || t.DecDegrees is < -90 or > 90)
                throw new ArgumentException("coordonnées hors limites");
            var m = registry.Mount(r.DeviceId);
            var s = m.GetState();
            if (s.Parked) throw new InvalidOperationException("monture parquée");
            if (s.Slewing) throw new InvalidOperationException("un slew est déjà en cours");

            if (!await m.SlewAsync(t.RaHours, t.DecDegrees, t.Epoch, c.CancellationToken))
                throw new RpcException(new Status(StatusCode.Aborted, "slew interrompu"));
            return new SlewResult { Completed = true, FinalState = m.GetState() };
        });

    public override Task<Empty> StopSlew(DeviceRef r, ServerCallContext c)
        => Task.FromResult(Guard(() => { registry.Mount(r.DeviceId).StopSlew(); return new Empty(); }));

    public override Task<Empty> Sync(SyncRequest r, ServerCallContext c)
        => Task.FromResult(Guard(() =>
        {
            var k = r.Coordinates;
            if (k.RaHours is < 0 or >= 24 || k.DecDegrees is < -90 or > 90)
                throw new ArgumentException("coordonnées hors limites");
            registry.Mount(r.DeviceId).Sync(k.RaHours, k.DecDegrees, k.Epoch);
            return new Empty();
        }));

    public override Task<MountState> SetTracking(SetTrackingRequest r, ServerCallContext c)
        => Task.FromResult(Guard(() =>
        {
            if (r.Mode == TrackingMode.Unspecified) throw new ArgumentException("mode de suivi non précisé");
            var m = registry.Mount(r.DeviceId);
            if (m.GetState().Parked && r.Mode != TrackingMode.Stopped) throw new InvalidOperationException("monture parquée");
            m.SetTracking(r.Mode, r.RaRate, r.DecRate);
            return m.GetState();
        }));

    public override Task<Empty> PulseGuide(PulseGuideRequest r, ServerCallContext c)
        => Task.FromResult(Guard(() =>
        {
            if (r.Direction == GuideDirection.Unspecified || r.DurationMs is 0 or > 10_000)
                throw new ArgumentException("direction ou durée invalide (1..10000 ms)");
            var m = registry.Mount(r.DeviceId);
            if (m.GetState().Parked) throw new InvalidOperationException("monture parquée");
            m.PulseGuide(r.Direction, (int)r.DurationMs);
            return new Empty();
        }));

    public override Task<Empty> MoveAxis(MoveAxisRequest r, ServerCallContext c)
        => Task.FromResult(Guard(() =>
        {
            if (r.Axis == Axis.Unspecified || Math.Abs(r.RateDegPerSec) > 5.0)
                throw new ArgumentException("axe ou vitesse invalide (|v| <= 5 deg/s)");
            var m = registry.Mount(r.DeviceId);
            if (m.GetState().Parked) throw new InvalidOperationException("monture parquée");
            m.MoveAxis(r.Axis, r.RateDegPerSec);
            return new Empty();
        }));

    public override async Task<MountState> Park(DeviceRef r, ServerCallContext c)
        => await GuardAsync(async () => { var m = registry.Mount(r.DeviceId); await m.ParkAsync(c.CancellationToken); return m.GetState(); });

    public override async Task<MountState> Unpark(DeviceRef r, ServerCallContext c)
        => await GuardAsync(async () => { var m = registry.Mount(r.DeviceId); await m.UnparkAsync(c.CancellationToken); return m.GetState(); });
}
