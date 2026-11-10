using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Unity.IL2CPP;
using Il2CppInterop.Runtime.Injection;
using UnityEngine;

namespace GoiBridge
{
    [BepInPlugin("dev.goibridge", "GoiBridge", "0.2.0")]
    public class Plugin : BasePlugin
    {
        internal static Plugin Instance;
        internal static ConfigEntry<int> SendPort, ListenPort;
        internal static ConfigEntry<KeyCode> DumpKey;
        internal static ConfigEntry<float> PivotLocalX, PivotLocalY;

        public override void Load()
        {
            Instance = this;
            SendPort = Config.Bind("Network", "SendPort", 9877, "UDP port on 127.0.0.1 the game state is sent TO.");
            ListenPort = Config.Bind("Network", "ListenPort", 9876, "UDP port on 127.0.0.1 commands are received ON.");
            PivotLocalX = Config.Bind("Pivot", "LocalX", 0f, "Hinge pivot offset in the local space of the hinge object (joint.anchor is not readable in this build).");
            PivotLocalY = Config.Bind("Pivot", "LocalY", 0f, "See LocalX.");
            DumpKey = Config.Bind("Debug", "DumpKey", KeyCode.F9, "Writes a state dump to BepInEx/goi_dump.txt");

            // No Harmony patches on purpose: they crashed the game. Everything is done from a plain component.
            ClassInjector.RegisterTypeInIl2Cpp<Bridge>();
            AddComponent<Bridge>();
            Log.LogInfo("GoiBridge 0.2.0 loaded.");
        }
    }

    public class Bridge : MonoBehaviour
    {
        public Bridge(IntPtr ptr) : base(ptr) { }

        enum Mode { None, World, Screen, Polar }
        Mode _mode = Mode.None;
        float _a, _b;               // meaning depends on mode
        int _method = 3;            // how the override is applied, see Apply(); 3 = cursor transform (best in tests)

        UdpClient _tx, _rx;
        IPEndPoint _target;
        PlayerControl _pc;
        float _nextLookup, _nextBeat, _nextErr;
        int _sent;

        // ---- settings (runtime only, forgotten when the game closes; the controller sets them via 'set <name> <value>') ----
        bool _blockPause;           // set block_pause 1: the player cannot open the pause menu / pause the game
        bool _ctrlPaused;           // pause 1: the controller has paused the game
        float _ctrlSavedScale = 1f; // Time.timeScale to restore on 'pause 0'
        bool _ctrlMenu;             // pause_menu 1: the controller opened the game's own pause menu
        float _menuCmdCooldown;     // ignore menu state right after a toggle, the game needs a frame or two to update
        SettingsManager _sm;
        float _nextSmLookup, _nextBlockLog;

        void Awake()
        {
            _target = new IPEndPoint(IPAddress.Loopback, Plugin.SendPort.Value);
            _tx = new UdpClient();
            _rx = new UdpClient(new IPEndPoint(IPAddress.Loopback, Plugin.ListenPort.Value));
            _rx.Client.Blocking = false;
            Application.runInBackground = true;   // keep Update/FixedUpdate running when the window loses focus
        }

        // ---------------- per-frame ----------------
        void Update()
        {
            try
            {
                if (Input.GetKeyDown(Plugin.DumpKey.Value)) Dump();
                // Re-assert in case the game (or a settings screen) turns it off again.
                if (!Application.runInBackground) Application.runInBackground = true;
                Lookup();
                ReadCommands();
                EnforceNoPause();
                Send();
                _sent++;
                if (Time.unscaledTime > _nextBeat)
                {
                    _nextBeat = Time.unscaledTime + 5f;
                    Plugin.Instance.Log.LogInfo("heartbeat: packets sent=" + _sent + " playerControl=" + (_pc != null) + " method=" + _method + " override=" + _mode);
                }
            }
            catch (Exception e)
            {
                if (Time.unscaledTime > _nextErr)
                {
                    _nextErr = Time.unscaledTime + 5f;
                    Plugin.Instance.Log.LogError("Update failed: " + e);
                }
            }
        }

        // Runs every physics step. The game's own FixedUpdate may run before/after us, so
        // the override reaches the hammer at most one physics step (20 ms) later.
        void FixedUpdate()
        {
            try { Apply(); }
            catch (Exception e) { Plugin.Instance.Log.LogWarning("Apply: " + e.Message); }
        }

        void LookupSm()
        {
            if (_sm != null) return;
            if (Time.unscaledTime < _nextSmLookup) return;
            _nextSmLookup = Time.unscaledTime + 1f;
            var found = UnityEngine.Object.FindObjectsOfType(Il2CppInterop.Runtime.Il2CppType.Of<SettingsManager>(), false);
            _sm = (found != null && found.Length > 0) ? found[0].TryCast<SettingsManager>() : null;
            if (_sm != null) Plugin.Instance.Log.LogInfo("Found SettingsManager on '" + _sm.gameObject.name + "'");
        }

        static bool ParseBool(string s)
        {
            s = s.ToLowerInvariant();
            return s == "1" || s == "true" || s == "on" || s == "yes";
        }

        // Called every frame (Update and LateUpdate). Layer 1 stops the game from opening its menu at all;
        // layer 2 closes it again (through the game's own ToggleMenu) if it got opened anyway.
        void EnforceNoPause()
        {
            // keep the 'opened by the controller' flag in sync, also when block_pause is off (the player may close the menu with Esc)
            if (_ctrlMenu && _pc != null && Time.unscaledTime > _menuCmdCooldown && !_pc.menuPause) _ctrlMenu = false;
            if (!_blockPause) return;
            LookupSm();
            if (_sm != null) _sm.canMenu = false;
            if (_ctrlPaused || _pc == null) return;
            if (_pc.menuPause && !_ctrlMenu && Time.unscaledTime > _menuCmdCooldown)
            {
                if (_sm != null) _sm.ToggleMenu();
                else _pc.UnPause();
                if (Time.timeScale == 0f) Time.timeScale = 1f;
                if (Time.unscaledTime > _nextBlockLog)
                {
                    _nextBlockLog = Time.unscaledTime + 2f;
                    Plugin.Instance.Log.LogInfo("block_pause: pause menu was opened by the player, closing it");
                }
            }
        }

        void LateUpdate()
        {
            try { EnforceNoPause(); }
            catch (Exception e) { Plugin.Instance.Log.LogWarning("EnforceNoPause: " + e.Message); }
        }

        // Opens/closes the game's own pause menu (same as pressing Esc) through its ToggleMenu().
        // Works regardless of block_pause: with block_pause 1 only the controller can close it again.
        void SetPauseMenu(bool open)
        {
            LookupSm(); Lookup();
            if (_sm == null || _pc == null) { Plugin.Instance.Log.LogWarning("pause_menu: game not ready (SettingsManager/PlayerControl not found)"); return; }
            if (Time.unscaledTime < _menuCmdCooldown) return;
            if (_pc.menuPause == open) { _ctrlMenu = open; return; }   // already in the requested state
            _ctrlMenu = open;                                          // set first so the block_pause watchdog leaves it alone
            _menuCmdCooldown = Time.unscaledTime + 0.3f;
            var old = _sm.canMenu;
            _sm.canMenu = true;                                        // in case ToggleMenu itself checks the flag
            _sm.ToggleMenu();
            _sm.canMenu = _blockPause ? false : old;
        }

        void SetControllerPause(bool pause)
        {
            if (pause == _ctrlPaused) return;
            _ctrlPaused = pause;
            if (pause)
            {
                _ctrlSavedScale = Time.timeScale > 0f ? Time.timeScale : 1f;
                Time.timeScale = 0f;
                AudioListener.pause = true;
            }
            else
            {
                Time.timeScale = _ctrlSavedScale;
                AudioListener.pause = false;
            }
        }

        void Lookup()
        {
            if (_pc != null) return;
            if (Time.unscaledTime < _nextLookup) return;
            _nextLookup = Time.unscaledTime + 1f;
            var found = UnityEngine.Object.FindObjectsOfType(Il2CppInterop.Runtime.Il2CppType.Of<PlayerControl>(), false);
            _pc = (found != null && found.Length > 0) ? found[0].TryCast<PlayerControl>() : null;
            if (_pc != null) Plugin.Instance.Log.LogInfo("Found PlayerControl on '" + _pc.gameObject.name + "'");
        }

        // ---------------- inbound ----------------
        //  W x y       world position of the cursor target (mw)
        //  M sx sy     screen pixels (origin bottom-left), converted to world
        //  H deg ext   hammer angle (degrees, 0 = +x, CCW) and distance from the pivot, in world units
        //  off         release override
        //  pause 1|0   controller pauses (1) / unpauses (0) the game; also accepts true/false/on/off
        //  pause_menu 1|0   open (1) / close (0) the game's own pause menu, like pressing Esc
        //  set block_pause 1|0   make it impossible (1) or possible (0, default) for the player to pause
        void ReadCommands()
        {
            while (_rx.Available > 0)
            {
                IPEndPoint any = null;
                var p = Encoding.ASCII.GetString(_rx.Receive(ref any)).Trim().Split(' ');
                try
                {
                    if (p[0] == "off") _mode = Mode.None;
                    else if (p[0] == "pause" && p.Length >= 2) SetControllerPause(ParseBool(p[1]));
                    else if (p[0] == "pause_menu" && p.Length >= 2) SetPauseMenu(ParseBool(p[1]));
                    else if (p[0] == "set" && p.Length >= 3)
                    {
                        var val = ParseBool(p[2]);
                        switch (p[1].ToLowerInvariant())
                        {
                            case "block_pause": _blockPause = val; if (!val && _sm != null) _sm.canMenu = true; break;
                            default: Plugin.Instance.Log.LogWarning("unknown setting: " + p[1]); break;
                        }
                    }
                    else if (p[0] == "X" && p.Length >= 2) _method = int.Parse(p[1], CultureInfo.InvariantCulture);
                    else if (p.Length >= 3)
                    {
                        _a = float.Parse(p[1], CultureInfo.InvariantCulture);
                        _b = float.Parse(p[2], CultureInfo.InvariantCulture);
                        _mode = p[0] == "W" ? Mode.World : p[0] == "M" ? Mode.Screen : p[0] == "H" ? Mode.Polar : _mode;
                    }
                }
                catch (Exception e) { Plugin.Instance.Log.LogWarning("bad command: " + e.Message); }
            }
        }

        static Vector3 Pivot(HingeJoint2D hj)
        {
            return hj.transform.TransformPoint(new Vector3(Plugin.PivotLocalX.Value, Plugin.PivotLocalY.Value, 0f));
        }

        static Vector2 ScreenToWorld(Camera cam, float sx, float sy, float planeZ)
        {
            var ray = cam.ScreenPointToRay(new Vector3(sx, sy, 0f));
            var o = ray.origin; var d = ray.direction;
            float t = MathF.Abs(d.z) > 1e-6f ? (planeZ - o.z) / d.z : 0f;
            return new Vector2(o.x + d.x * t, o.y + d.y * t);
        }

        void Apply()
        {
            if (_mode == Mode.None || _pc == null) return;
            Vector2 w;
            if (_mode == Mode.World) w = new Vector2(_a, _b);
            else if (_mode == Mode.Screen)
            {
                var cam = Camera.main; if (cam == null) return;
                w = ScreenToWorld(cam, _a, _b, _pc.fakeCursor.position.z);
            }
            else
            {
                var c = Pivot(_pc.hj);
                float r = _b, th = _a * (MathF.PI / 180f);
                w = new Vector2(c.x + r * MathF.Cos(th), c.y + r * MathF.Sin(th));
            }
            var rb = _pc.fakeCursorRB;
            switch (_method)
            {
                case 0: _pc.mw = w; break;                                        // the game's cursor target field
                case 1: rb.position = w; break;                                   // cursor rigidbody, teleport
                case 2: rb.MovePosition(w); break;                                // cursor rigidbody, physics move
                case 3: { var p = _pc.fakeCursor.position; _pc.fakeCursor.position = new Vector3(w.x, w.y, p.z); break; } // transform
                case 4:                                                           // everything at once
                    _pc.mw = w; rb.position = w;
                    { var p = _pc.fakeCursor.position; _pc.fakeCursor.position = new Vector3(w.x, w.y, p.z); }
                    break;
                case 5: rb.velocity = (w - rb.position) * 50f; break;             // chase with velocity (1/0.02s)
            }
        }

        // ---------------- outbound ----------------
        static string F(float f) => f.ToString("R", CultureInfo.InvariantCulture);

        void Send()
        {
            var sb = new StringBuilder(384);
            sb.Append("{\"t\":").Append(F(Time.unscaledTime));
            sb.Append(",\"sw\":").Append(Screen.width).Append(",\"sh\":").Append(Screen.height);
            sb.Append(",\"ov\":").Append(_mode != Mode.None ? "true" : "false");
            sb.Append(",\"bp\":").Append(_blockPause ? "true" : "false");      // setting: block_pause
            sb.Append(",\"pz\":").Append(_ctrlPaused ? "true" : "false");      // paused by the controller
            sb.Append(",\"pm\":").Append(_ctrlMenu ? "true" : "false");      // pause menu opened by the controller
            sb.Append(",\"ts\":").Append(F(Time.timeScale));                      // Time.timeScale
            if (_pc != null) sb.Append(",\"mp\":").Append(_pc.menuPause ? "true" : "false"); // game's own menu-pause flag
            sb.Append(",\"fo\":").Append(Application.isFocused ? "true" : "false");     // window focused?
            sb.Append(",\"bg\":").Append(Application.runInBackground ? "true" : "false"); // keeps running unfocused?
            if (_pc != null)
            {
                var fc = _pc.fakeCursor.position;          // in-game cursor, world
                sb.Append(",\"fx\":").Append(F(fc.x)).Append(",\"fy\":").Append(F(fc.y));
                var cam = Camera.main;
                if (cam != null)
                {
                    var s = cam.WorldToScreenPoint(fc);    // in-game cursor, screen pixels
                    sb.Append(",\"mx\":").Append(F(s.x)).Append(",\"my\":").Append(F(s.y));
                }
                sb.Append(",\"mt\":").Append(_method);
                var crb = _pc.fakeCursorRB; var cp = crb.position; var cv = crb.velocity;
                sb.Append(",\"rx\":").Append(F(cp.x)).Append(",\"ry\":").Append(F(cp.y));
                sb.Append(",\"vx\":").Append(F(cv.x)).Append(",\"vy\":").Append(F(cv.y));
                var mw = _pc.mw;
                sb.Append(",\"wx\":").Append(F(mw.x)).Append(",\"wy\":").Append(F(mw.y));
                var tip = _pc.tip.position;
                sb.Append(",\"tx\":").Append(F(tip.x)).Append(",\"ty\":").Append(F(tip.y));
                var c = Pivot(_pc.hj);                     // centre of rotation, world
                sb.Append(",\"cx\":").Append(F(c.x)).Append(",\"cy\":").Append(F(c.y));
                sb.Append(",\"ha\":").Append(F(_pc.hj.jointAngle));         // hinge angle (deg)
                sb.Append(",\"st\":").Append(F(_pc.sj.jointTranslation));   // slider extension
                var ht = _pc.hj.transform;                  // object carrying the hinge joint
                var hp = ht.position; var q = ht.rotation;
                sb.Append(",\"bx\":").Append(F(hp.x)).Append(",\"by\":").Append(F(hp.y));
                sb.Append(",\"br\":").Append(F(2f * MathF.Atan2(q.z, q.w) * (180f / MathF.PI)));
            }
            sb.Append('}');
            var b = Encoding.ASCII.GetBytes(sb.ToString());
            _tx.Send(b, b.Length, _target);
        }

        // ---------------- discovery ----------------
        void Dump()
        {
            var sb = new StringBuilder();
            sb.AppendLine("Screen " + Screen.width + "x" + Screen.height + " cursor lock=" + Cursor.lockState);
            if (_pc != null)
            {
                sb.AppendLine("PlayerControl on " + _pc.gameObject.name);
                sb.AppendLine("mw=" + _pc.mw + " mouseInput=" + _pc.mouseInput + " oldMouse=" + _pc.oldMouse + " sens=" + _pc.mouseSensitivity);
                sb.AppendLine("fakeCursor=" + _pc.fakeCursor.position + " tip=" + _pc.tip.position);
                var hj = _pc.hj; var sj = _pc.sj;
                sb.AppendLine("hj on '" + hj.gameObject.name + "' pos=" + hj.transform.position + " rot=" + hj.transform.rotation
                    + " jointAngle=" + hj.jointAngle + " pivotWorld(offset from config)=" + Pivot(hj));
                sb.AppendLine("sj on '" + sj.gameObject.name + "' pos=" + sj.transform.position + " translation=" + sj.jointTranslation);
                sb.AppendLine("hammerCollider on '" + _pc.hammerCollider.gameObject.name + "'");
            }
            if (_sm != null) sb.AppendLine("SettingsManager on '" + _sm.gameObject.name + "' canMenu=" + _sm.canMenu);
            sb.AppendLine("timeScale=" + Time.timeScale + " blockPause=" + _blockPause + " ctrlPaused=" + _ctrlPaused);
            if (_pc == null) sb.AppendLine("PlayerControl NOT found (load into the game first)");
            var path = Path.Combine(Paths.BepInExRootPath, "goi_dump.txt");
            File.WriteAllText(path, sb.ToString());
            Plugin.Instance.Log.LogInfo("Dump written to " + path);
        }
    }
}