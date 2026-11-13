using Grpc.Core;
using StarTracker.Devices.V1;
using static StarTracker.DeviceService.Services.ErrorMapping;

namespace StarTracker.DeviceService.Services;

public sealed class DeviceGrpcService(DeviceRegistry registry) : Devices.V1.DeviceService.DeviceServiceBase
{
    public override Task<ListDevicesResponse> ListDevices(ListDevicesRequest request, ServerCallContext context)
    {
        // SPIKE : si request.Rescan, relancer BackendDiscovery.FindMountsAsync.
        var res = new ListDevicesResponse();
        if (request.Kind is DeviceKind.Unspecified or DeviceKind.Mount)
            foreach (var m in registry.Mounts)
                res.Devices.Add(new DeviceInfo { Id = m.Id, Kind = DeviceKind.Mount, Name = m.Name, Backend = m.BackendName, Connected = m.Connected });
        return Task.FromResult(res);
    }

    public override async Task<DeviceInfo> Connect(DeviceRef request, ServerCallContext context)
        => await GuardAsync(async () =>
        {
            var m = registry.Mount(request.DeviceId, requireConnected: false);
            if (!await m.ConnectAsync(context.CancellationToken))
                throw new RpcException(new Status(StatusCode.Unavailable, $"connexion à {m.Name} impossible"));
            return new DeviceInfo { Id = m.Id, Kind = DeviceKind.Mount, Name = m.Name, Backend = m.BackendName, Connected = m.Connected };
        });

    public override Task<DeviceInfo> Disconnect(DeviceRef request, ServerCallContext context)
        => Task.FromResult(Guard(() =>
        {
            var m = registry.Mount(request.DeviceId, requireConnected: false);
            m.Disconnect();
            return new DeviceInfo { Id = m.Id, Kind = DeviceKind.Mount, Name = m.Name, Backend = m.BackendName, Connected = m.Connected };
        }));
}
