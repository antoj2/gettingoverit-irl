using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

namespace PhysicalHammerMod;

[BepInPlugin(
    MyPluginInfo.PLUGIN_GUID,
    MyPluginInfo.PLUGIN_NAME,
    MyPluginInfo.PLUGIN_VERSION)]
public class Plugin : BasePlugin
{
    internal static new ManualLogSource Log;

    public override void Load()
    {
        Log = base.Log;

        Log.LogInfo($"Plugin {MyPluginInfo.PLUGIN_GUID} is loaded!");

        var harmony = new Harmony(MyPluginInfo.PLUGIN_GUID);
        harmony.PatchAll();

        Log.LogInfo("Harmony patches applied.");
    }
}

[HarmonyPatch(typeof(PlayerControl), nameof(PlayerControl.FixedUpdate))]
internal static class PlayerControlFixedUpdatePatch
{
    private static bool _logged;

    private static void Prefix(PlayerControl __instance)
    {
        if (_logged)
            return;

        _logged = true;

        Plugin.Log.LogInfo(
            $"PlayerControl.FixedUpdate hooked! " +
            $"Current mouseInput = {__instance.mouseInput}"
        );
    }
}