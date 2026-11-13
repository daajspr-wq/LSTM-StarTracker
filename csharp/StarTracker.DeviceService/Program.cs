using Microsoft.AspNetCore.Server.Kestrel.Core;
using StarTracker.DeviceService.Backends;
using StarTracker.DeviceService.Services;

var builder = WebApplication.CreateBuilder(args);

// Écoute locale uniquement, HTTP/2 sans TLS (le service n'est jamais exposé au réseau).
builder.WebHost.ConfigureKestrel(k =>
    k.ListenLocalhost(builder.Configuration.GetValue("Port", 50051), o => o.Protocols = HttpProtocols.Http2));

builder.Services.AddGrpc();
builder.Services.AddSingleton<DeviceRegistry>();

var app = builder.Build();

// Découverte au démarrage (ASCOM + Alpaca). Sans NINA : registre vide.
var registry = app.Services.GetRequiredService<DeviceRegistry>();
foreach (var mount in await BackendDiscovery.FindMountsAsync(app.Lifetime.ApplicationStopping))
    registry.Add(mount);

app.MapGrpcService<DeviceGrpcService>();
app.MapGrpcService<MountGrpcService>();
// TODO : CameraGrpcService (même schéma que MountGrpcService, voir README).

app.Run();
