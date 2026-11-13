using Grpc.Core;
using StarTracker.DeviceService.Backends;

namespace StarTracker.DeviceService.Services;

/// <summary>Appareils connus du service. Les caméras suivront le même schéma que les montures.</summary>
public sealed class DeviceRegistry
{
    private readonly Dictionary<string, IMountBackend> _mounts = new();

    public void Add(IMountBackend m) => _mounts[m.Id] = m;
    public IEnumerable<IMountBackend> Mounts => _mounts.Values;

    public IMountBackend Mount(string id, bool requireConnected = true)
    {
        if (!_mounts.TryGetValue(id, out var m))
            throw new RpcException(new Status(StatusCode.NotFound, $"device_id inconnu : '{id}'"));
        if (requireConnected && !m.Connected)
            throw new RpcException(new Status(StatusCode.FailedPrecondition, $"{id} n'est pas connecté"));
        return m;
    }
}
