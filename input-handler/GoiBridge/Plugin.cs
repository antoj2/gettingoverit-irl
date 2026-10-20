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
        int _method;                // how the override is applied, see Apply()

        UdpClient _tx, _rx;
        IPEndPoint _target;
        PlayerControl _pc;
        float _nextLookup, _nextBeat, _nextErr;
        int _sent;

        void Awake()
        {
            _target = new IPEndPoint(IPAddress.Loopback, Plugin.SendPort.Value);
            _tx = new UdpClient();
            _rx = new UdpClient(new IPEndPoint(IPAddress.Loopback, Plugin.ListenPort.Value));
            _rx.Client.Blocking = false;
        }

        // ---------------- per-frame ----------------
        void Update()
        {
            try
            {
                if (Input.GetKeyDown(Plugin.DumpKey.Value)) Dump();
                Lookup();
                ReadCommands();
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
        void ReadCommands()
        {
            while (_rx.Available > 0)
            {
                IPEndPoint any = null;
                var p = Encoding.ASCII.GetString(_rx.Receive(ref any)).Trim().Split(' ');
                try
                {
                    if (p[0] == "off") _mode = Mode.None;
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
            else sb.AppendLine("PlayerControl NOT found (load into the game first)");
            var path = Path.Combine(Paths.BepInExRootPath, "goi_dump.txt");
            File.WriteAllText(path, sb.ToString());
            Plugin.Instance.Log.LogInfo("Dump written to " + path);
        }
    }
}