using StarTracker.Devices.V1;

namespace StarTracker.DeviceService.Backends;

/// <summary>
/// Ce que le service attend d'une monture, indépendamment de NINA/ASCOM/Alpaca.
/// Les types de NINA ne doivent JAMAIS sortir de NinaMountBackend.
/// </summary>
public interface IMountBackend
{
    string Id { get; }
    string Name { get; }
    string BackendName { get; }          // "ascom" | "alpaca" | ...
    bool Connected { get; }

    Task<bool> ConnectAsync(CancellationToken ct);
    void Disconnect();

    MountInfo GetInfo();
    MountState GetState();

    /// <returns>true si le pointage est terminé, false s'il a été interrompu.</returns>
    Task<bool> SlewAsync(double raHours, double decDegrees, Devices.V1.Epoch epoch, CancellationToken ct);
    void StopSlew();
    void Sync(double raHours, double decDegrees, Devices.V1.Epoch epoch);
    void SetTracking(TrackingMode mode, double raRate, double decRate);
    void PulseGuide(GuideDirection direction, int durationMs);
    void MoveAxis(Axis axis, double rateDegPerSec);
    Task ParkAsync(CancellationToken ct);
    Task UnparkAsync(CancellationToken ct);
}
