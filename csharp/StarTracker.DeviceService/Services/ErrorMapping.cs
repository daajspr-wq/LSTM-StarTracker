using System.Runtime.InteropServices;
using Grpc.Core;

namespace StarTracker.DeviceService.Services;

/// <summary>Exceptions .NET/ASCOM -> codes gRPC documentés en tête de devices.proto.</summary>
public static class ErrorMapping
{
    public static RpcException ToRpc(Exception ex) => ex switch
    {
        RpcException r => r,
        OperationCanceledException => new(new Status(StatusCode.Aborted, "opération interrompue")),
        NotSupportedException or NotImplementedException => new(new Status(StatusCode.Unimplemented, ex.Message)),
        InvalidOperationException => new(new Status(StatusCode.FailedPrecondition, ex.Message)),
        ArgumentException => new(new Status(StatusCode.InvalidArgument, ex.Message)),
        TimeoutException => new(new Status(StatusCode.DeadlineExceeded, ex.Message)),
        // SPIKE : affiner avec les exceptions réellement levées par les drivers (COM/ASCOM, Alpaca HTTP).
        COMException or IOException => new(new Status(StatusCode.Unavailable, ex.Message)),
        _ => new(new Status(StatusCode.Internal, ex.ToString())),
    };

    public static T Guard<T>(Func<T> f)
    {
        try { return f(); } catch (Exception ex) { throw ToRpc(ex); }
    }

    public static async Task<T> GuardAsync<T>(Func<Task<T>> f)
    {
        try { return await f(); } catch (Exception ex) { throw ToRpc(ex); }
    }
}
