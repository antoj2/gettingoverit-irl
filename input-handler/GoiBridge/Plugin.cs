using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using Il2CppInterop.Runtime.Injection;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace GoiBridge
{
    [BepInPlugin("dev.goibridge", "GoiBridge", "0.1.0")]
    public class Plugin : BasePlugin
    {
        internal static Plugin Instance;
        internal static ConfigEntry<int> SendPort, ListenPort;
        internal static ConfigEntry<string> PlayerName, HammerName;
        internal static ConfigEntry<bool> PatchInputMouse;
        internal static ConfigEntry<KeyCode> DumpKey;

        public override void Load()
        {
            Instance = this;
            SendPort = Config.Bind("Network", "SendPort", 9877, "UDP port on 127.0.0.1 the game state is sent TO.");
            ListenPort = Config.Bind("Network", "ListenPort", 9876, "UDP port on 127.0.0.1 commands are received ON.");
            PlayerName = Config.Bind("Tracking", "PlayerObjectName", "", "Exact GameObject name of the player body (fill in after F9 dump).");
            HammerName = Config.Bind("Tracking", "HammerObjectName", "", "Exact GameObject name of the hammer (fill in after F9 dump).");
            PatchInputMouse = Config.Bind("Override", "PatchInputMousePosition", true,
                "Make UnityEngine.Input.mousePosition return the overridden value while an override is active.");
            DumpKey = Config.Bind("Debug", "DumpKey", KeyCode.F9, "Writes scene/physics dump to BepInEx/goi_dump.txt");

            ClassInjector.RegisterTypeInIl2Cpp<Bridge>();
            AddComponent<Bridge>();

            if (PatchInputMouse.Value)
            {
                try { new Harmony("dev.goibridge").PatchAll(typeof(MousePatch)); Log.LogInfo("Input.mousePosition patched."); }
                catch (Exception e) { Log.LogError("Mouse patch failed: " + e); }
            }
            Log.LogInfo("GoiBridge loaded. Press " + DumpKey.Value + " in-game to dump the scene.");
        }
    }

    // Input.mousePosition override (item 3). Only affects code that reads UnityEngine.Input.
    // If the game reads the mouse through Rewired instead, this will have no visible effect (see README).
    [HarmonyPatch]
    static class MousePatch
    {
        static System.Reflection.MethodBase TargetMethod() =>
            AccessTools.PropertyGetter(typeof(Input), nameof(Input.mousePosition));
        static void Postfix(ref Vector3 __result)
        {
            if (Bridge.MouseOverride.HasValue)
            {
                var v = Bridge.MouseOverride.Value;
                __result = new Vector3(v.x, v.y, 0f);
            }
        }
    }

    public class Bridge : MonoBehaviour
    {
        public Bridge(IntPtr ptr) : base(ptr) { }

        public static Vector2? MouseOverride;

        UdpClient _tx, _rx;
        IPEndPoint _target;
        Transform _player, _hammer;
        Rigidbody2D _playerRb, _hammerRb;
        float _nextLookup;

        void Awake()
        {
            _target = new IPEndPoint(IPAddress.Loopback, Plugin.SendPort.Value);
            _tx = new UdpClient();
            _rx = new UdpClient(new IPEndPoint(IPAddress.Loopback, Plugin.ListenPort.Value));
            _rx.Client.Blocking = false;
        }

        void Update()
        {
            if (Input.GetKeyDown(Plugin.DumpKey.Value)) Dump();
            ReadCommands();
            Lookup();
            Send();
        }

        // ---------- inbound ----------
        // "M x y"  -> override mouse position (screen pixels, origin bottom-left)
        // "M off"  -> release override
        void ReadCommands()
        {
            try
            {
                while (_rx.Available > 0)
                {
                    IPEndPoint any = null;
                    var text = Encoding.ASCII.GetString(_rx.Receive(ref any)).Trim();
                    var p = text.Split(' ');
                    if (p.Length >= 2 && p[0] == "M")
                    {
                        if (p[1] == "off") MouseOverride = null;
                        else if (p.Length >= 3)
                            MouseOverride = new Vector2(
                                float.Parse(p[1], CultureInfo.InvariantCulture),
                                float.Parse(p[2], CultureInfo.InvariantCulture));
                    }
                }
            }
            catch (Exception e) { Plugin.Instance.Log.LogWarning("cmd: " + e.Message); }
        }

        // ---------- lookup ----------
        void Lookup()
        {
            if (Time.unscaledTime < _nextLookup) return;
            _nextLookup = Time.unscaledTime + 1f;
            if (_player == null && Plugin.PlayerName.Value != "")
            {
                var g = GameObject.Find(Plugin.PlayerName.Value);
                if (g != null) { _player = g.transform; _playerRb = g.GetComponent<Rigidbody2D>(); }
            }
            if (_hammer == null && Plugin.HammerName.Value != "")
            {
                var g = GameObject.Find(Plugin.HammerName.Value);
                if (g != null) { _hammer = g.transform; _hammerRb = g.GetComponent<Rigidbody2D>(); }
            }
        }

        // ---------- outbound ----------
        static string F(float f) => f.ToString("R", CultureInfo.InvariantCulture);

        void Send()
        {
            try
            {
                var m = Input.mousePosition; // note: reflects override if active
                var sb = new StringBuilder(256);
                sb.Append("{\"t\":").Append(F(Time.unscaledTime));
                sb.Append(",\"sw\":").Append(Screen.width).Append(",\"sh\":").Append(Screen.height);
                sb.Append(",\"mx\":").Append(F(m.x)).Append(",\"my\":").Append(F(m.y));
                sb.Append(",\"ov\":").Append(MouseOverride.HasValue ? "true" : "false");
                Append(sb, "p", _player, _playerRb);
                Append(sb, "h", _hammer, _hammerRb);
                sb.Append('}');
                var b = Encoding.ASCII.GetBytes(sb.ToString());
                _tx.Send(b, b.Length, _target);
            }
            catch (Exception) { }
        }

        static void Append(StringBuilder sb, string k, Transform t, Rigidbody2D rb)
        {
            if (t == null) return;
            var pos = t.position;
            float rot = rb != null ? rb.rotation : t.eulerAngles.z;
            sb.Append(",\"").Append(k).Append("x\":").Append(F(pos.x));
            sb.Append(",\"").Append(k).Append("y\":").Append(F(pos.y));
            sb.Append(",\"").Append(k).Append("r\":").Append(F(rot));
        }

        // ---------- discovery ----------
        void Dump()
        {
            var path = Path.Combine(Paths.BepInExRootPath, "goi_dump.txt");
            var sb = new StringBuilder();
            sb.AppendLine("Screen " + Screen.width + "x" + Screen.height + "  Input.mousePosition=" + Input.mousePosition);
            sb.AppendLine("Cursor lock=" + Cursor.lockState + " visible=" + Cursor.visible);
            sb.AppendLine("\n== Rigidbody2D ==");
            foreach (var rb in UnityEngine.Object.FindObjectsOfType<Rigidbody2D>())
            {
                sb.AppendFormat("{0}  pos={1} rot={2} type={3} parent={4}\n",
                    Full(rb.transform), rb.position, rb.rotation, rb.bodyType,
                    rb.transform.parent != null ? rb.transform.parent.name : "-");
                foreach (var c in rb.gameObject.GetComponents<Component>())
                    sb.AppendLine("     - " + c.GetIl2CppType().FullName);
            }
            sb.AppendLine("\n== Root objects and their components ==");
            for (int i = 0; i < SceneManager.sceneCount; i++)
                foreach (var go in SceneManager.GetSceneAt(i).GetRootGameObjects())
                {
                    sb.AppendLine(go.name);
                    foreach (var c in go.GetComponents<Component>())
                        sb.AppendLine("     - " + c.GetIl2CppType().FullName);
                }
            File.WriteAllText(path, sb.ToString());
            Plugin.Instance.Log.LogInfo("Dump written to " + path);
        }

        static string Full(Transform t)
        {
            var s = t.name;
            while (t.parent != null) { t = t.parent; s = t.name + "/" + s; }
            return s;
        }
    }
}