#if NINA
using NINA.Equipment.Utility;
using NINA.Profile.Interfaces;
#endif

namespace StarTracker.DeviceService.Backends;

public static class BackendDiscovery
{
    public static async Task<IReadOnlyList<IMountBackend>> FindMountsAsync(CancellationToken ct)
    {
        var found = new List<IMountBackend>();
#if NINA
        // SPIKE : ASCOMInteraction / AlpacaInteraction demandent un IProfileService (NINA.Profile).
        // À obtenir via ProfileService, ou à implémenter minimalement s'il est trop lourd à instancier.
        IProfileService profile = CreateProfileService();

        // Même logique que TelescopeChooserVM dans NINA.WPF.Base, sans passer par le ViewModel.
        try { foreach (var t in new ASCOMInteraction(profile).GetTelescopes()) found.Add(new NinaMountBackend(t, "ascom")); }
        catch (Exception ex) { Console.Error.WriteLine($"Découverte ASCOM : {ex.Message}"); }

        try { foreach (var t in await new AlpacaInteraction(profile).GetTelescopes(ct)) found.Add(new NinaMountBackend(t, "alpaca")); }
        catch (Exception ex) { Console.Error.WriteLine($"Découverte Alpaca : {ex.Message}"); }
#else
        await Task.CompletedTask;
        Console.Error.WriteLine("Compilé sans NINA (-p:NinaRoot=...) : aucun appareil exposé.");
#endif
        return found;
    }

#if NINA
    private static IProfileService CreateProfileService()
        => throw new NotImplementedException("SPIKE : fournir un IProfileService (voir NINA.Profile).");
#endif
}
