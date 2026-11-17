import Cairo from 'cairo';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const PORT = 45454;
const EVENT_PORT = 45455; // mouse events are sent here while interactive

function rgba(s, def = [1, 1, 1, 1]) {
    if (typeof s !== 'string' || !/^#([0-9a-f]{6}|[0-9a-f]{8})$/i.test(s)) return def;
    const h = s.slice(1);
    const i = h.length === 8 ? 2 : 0;
    const a = i ? parseInt(h.slice(0, 2), 16) / 255 : 1;
    const c = k => parseInt(h.slice(i + k, i + k + 2), 16) / 255;
    return [c(0), c(2), c(4), a];
}

function roundRect(cr, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    cr.newSubPath();
    cr.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    cr.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    cr.arc(x + r, y + r, r, Math.PI, 1.5 * Math.PI);
    cr.closePath();
}

export default class GoiOverlay extends Extension {
    enable() {
        this._items = [];
        this._visible = true;
        this._auto = 0;

        this._interactive = false;
        this._area = new St.DrawingArea({reactive: false, can_focus: false, track_hover: false});
        this._area.connect('repaint', a => this._paint(a));
        this._area.connect('button-press-event', (_a, e) => this._mouse('press', e));
        this._area.connect('button-release-event', (_a, e) => this._mouse('release', e));
        this._area.connect('motion-event', (_a, e) => this._mouse('move', e));
        this._monIndex = Main.layoutManager.primaryIndex;
        this._place();
        this._attach();
        // Fullscreen windows are normally "unredirected" (drawn straight to the screen),
        // which hides everything the shell draws above them, including this overlay.
        try { global.compositor.disable_unredirect(); this._unredirectOff = true; } catch (e) {
            console.error(`goi-overlay: cannot disable unredirect: ${e.message}`);
        }
        this._monSig = Main.layoutManager.connect('monitors-changed', () => { this._place(); this._area.queue_repaint(); });
        this._evAddr = Gio.InetSocketAddress.new_from_string('127.0.0.1', EVENT_PORT);

        this._socket = Gio.Socket.new(Gio.SocketFamily.IPV4, Gio.SocketType.DATAGRAM, Gio.SocketProtocol.UDP);
        this._socket.set_blocking(false);
        try {
            this._socket.bind(Gio.InetSocketAddress.new_from_string('127.0.0.1', PORT), true);
            this._src = this._socket.create_source(GLib.IOCondition.IN, null);
            this._src.set_callback(() => { this._read(); return GLib.SOURCE_CONTINUE; });
            this._src.attach(null);
        } catch (e) {
            console.error(`goi-overlay: cannot bind UDP ${PORT}: ${e.message}`);
        }

        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
            const now = GLib.get_monotonic_time() / 1000;
            const n = this._items.length;
            this._items = this._items.filter(i => !i.expires || i.expires > now);
            if (this._items.length !== n) this._area.queue_repaint();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._unredirectOff) { try { global.compositor.enable_unredirect(); } catch (_e) { /* ignore */ } }
        this._unredirectOff = false;
        if (this._monSig) Main.layoutManager.disconnect(this._monSig);
        if (this._timer) GLib.source_remove(this._timer);
        if (this._src) this._src.destroy();
        if (this._socket) this._socket.close();
        if (this._area) { this._detach(); this._area.destroy(); }
        this._timer = this._src = this._socket = this._area = this._items = this._monSig = null;
    }

    _read() {
        const dec = new TextDecoder();
        for (;;) {
            let bytes;
            try { bytes = this._socket.receive_bytes(65536, 0, null); } catch (_e) { break; }
            for (const line of dec.decode(bytes.toArray()).split('\n')) {
                if (!line.trim()) continue;
                try { this._handle(JSON.parse(line)); } catch (e) { console.error(`goi-overlay: ${e.message}`); }
            }
        }
        this._area.queue_repaint();
    }

    _place() {
        const mons = Main.layoutManager.monitors;
        if (this._monIndex < 0 || this._monIndex >= mons.length) this._monIndex = Main.layoutManager.primaryIndex;
        const m = mons[this._monIndex];
        this._mon = m;
        this._area.set_position(m.x, m.y);
        this._area.set_size(m.width, m.height);
    }

    // Option names of addChrome() differ between GNOME versions, so avoid them:
    // - click-through: add to uiGroup directly (not chrome => not in the input region)
    // - interactive: addChrome() with defaults (tracked => receives input)
    // Either way the actor is stacked above all windows, including fullscreen ones.
    _attach() {
        const lm = Main.layoutManager;
        this._area.reactive = this._interactive;
        if (this._interactive) {
            lm.addChrome(this._area);
        } else {
            lm.uiGroup.add_child(this._area);
            if (lm.uiGroup.contains(global.top_window_group))
                lm.uiGroup.set_child_below_sibling(this._area, global.top_window_group);
        }
    }

    _detach() {
        if (!this._area.get_parent()) return;
        if (this._interactive) Main.layoutManager.removeChrome(this._area);
        else Main.layoutManager.uiGroup.remove_child(this._area);
    }

    _setInteractive(on) {
        if (on === this._interactive) return;
        this._detach();
        this._interactive = on;
        this._attach();
    }

    _mouse(type, e) {
        const [x, y] = e.get_coords();
        const b = type === 'move' ? 0 : e.get_button();
        const m = this._mon;
        const msg = {event: 'mouse', type, button: b === 1 ? 'left' : b === 3 ? 'right' : b === 2 ? 'middle' : 'none',
                     x: (x - m.x) / m.width, y: (y - m.y) / m.height};
        try { this._socket.send_to(this._evAddr, new TextEncoder().encode(JSON.stringify(msg) + '\n'), null); } catch (_e) { /* ignore */ }
        return Clutter.EVENT_STOP;
    }

    _upsert(o) {
        const id = o.id || `_auto${this._auto++}`;
        const it = {id, p: o, expires: o.ttl > 0 ? GLib.get_monotonic_time() / 1000 + o.ttl : 0};
        const k = this._items.findIndex(e => e.id === id);
        if (k >= 0) this._items[k] = it; else this._items.push(it);
    }

    _handle(m) {
        switch (m.cmd ?? 'set') {
        case 'clear': this._items = []; break;
        case 'remove': this._items = this._items.filter(i => i.id !== m.id); break;
        case 'screen': this._monIndex = m.index ?? 0; this._place(); break;
        case 'interactive': this._setInteractive(m.enabled ?? true); break;
        case 'hide': this._visible = false; break;
        case 'show': this._visible = true; break;
        case 'batch': (m.items ?? []).forEach(o => this._upsert(o)); break;
        default: this._upsert(m);
        }
    }

    _paint(area) {
        const cr = area.get_context();
        const [W, H] = area.get_surface_size();
        if (this._visible) for (const it of this._items) this._draw(cr, it.p, W, H);
        cr.$dispose();
    }

    _draw(cr, o, W, H) {
        const type = o.type ?? 'text';
        const num = (k, d) => (typeof o[k] === 'number' ? o[k] : d);
        const lw = Math.max(1, num('width', 0.004) * H);
        cr.setLineCap(Cairo.LineCap.ROUND);
        cr.setLineJoin(Cairo.LineJoin.ROUND);

        if (type === 'line') {
            const ax = num('x1', 0) * W, ay = num('y1', 0) * H, bx = num('x2', 0) * W, by = num('y2', 0) * H;
            cr.setSourceRGBA(...rgba(o.color));
            cr.setLineWidth(lw);
            cr.moveTo(ax, ay); cr.lineTo(bx, by);
            if (o.arrow) {
                const ang = Math.atan2(by - ay, bx - ax), len = 0.025 * H;
                for (const s of [-0.45, 0.45]) {
                    cr.moveTo(bx, by);
                    cr.lineTo(bx - Math.cos(ang + s) * len, by - Math.sin(ang + s) * len);
                }
            }
            cr.stroke();
        } else if (type === 'rect' || type === 'circle') {
            if (type === 'rect')
                roundRect(cr, num('x', 0) * W, num('y', 0) * H, num('w', 0.1) * W, num('h', 0.1) * H, num('radius', 0) * H);
            else
                cr.arc(num('x', 0.5) * W, num('y', 0.5) * H, num('r', 0.05) * H, 0, 2 * Math.PI);
            if (o.fill) { cr.setSourceRGBA(...rgba(o.fill)); cr.fillPreserve(); }
            if (num('width', 0.004) > 0) { cr.setSourceRGBA(...rgba(o.color)); cr.setLineWidth(lw); cr.stroke(); }
            else cr.newPath();
        } else {
            const lines = String(o.text ?? '').split('\n');
            const size = Math.max(4, num('size', 0.03) * H), lh = size * 1.2;
            cr.selectFontFace('sans-serif', Cairo.FontSlant.NORMAL, o.bold ? Cairo.FontWeight.BOLD : Cairo.FontWeight.NORMAL);
            cr.setFontSize(size);
            const widths = lines.map(l => cr.textExtents(l).xAdvance);
            const tw = Math.max(...widths), th = lh * lines.length;
            const align = o.align ?? 'left';
            let x = num('x', 0) * W;
            if (align === 'center') x -= tw / 2; else if (align === 'right') x -= tw;
            const y = num('y', 0) * H;
            if (o.bg) {
                const pad = 0.01 * H;
                roundRect(cr, x - pad, y - pad / 2, tw + 2 * pad, th + pad, pad);
                cr.setSourceRGBA(...rgba(o.bg, [0, 0, 0, 0.63]));
                cr.fill();
            }
            lines.forEach((l, i) => {
                const lx = x + (align === 'center' ? (tw - widths[i]) / 2 : align === 'right' ? tw - widths[i] : 0);
                const ly = y + size * 0.9 + i * lh;
                if (o.shadow !== false) {
                    cr.setSourceRGBA(0, 0, 0, 0.78);
                    cr.moveTo(lx + 0.002 * H, ly + 0.002 * H); cr.showText(l);
                }
                cr.setSourceRGBA(...rgba(o.color));
                cr.moveTo(lx, ly); cr.showText(l);
            });
        }
    }
}
