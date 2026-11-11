#include "Overlay.h"
#include <QJsonArray>
#include <QPainter>
#include <QPainterPath>
#include <QTimer>
#include <cmath>

Overlay::Overlay(QWidget *parent) : QWidget(parent) {
    setWindowFlags(Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint | Qt::Tool |
                   Qt::WindowTransparentForInput | Qt::WindowDoesNotAcceptFocus |
                   Qt::NoDropShadowWindowHint);
    setAttribute(Qt::WA_TranslucentBackground);
    setAttribute(Qt::WA_ShowWithoutActivating);
    setAttribute(Qt::WA_TransparentForMouseEvents);
    setFocusPolicy(Qt::NoFocus);
    clock_.start();

    auto *t = new QTimer(this);
    connect(t, &QTimer::timeout, this, [this] {
        const qint64 now = clock_.elapsed();
        const int before = items_.size();
        items_.erase(std::remove_if(items_.begin(), items_.end(),
                                    [&](const Item &i) { return i.expires && i.expires <= now; }),
                     items_.end());
        if (items_.size() != before) update();
    });
    t->start(33);
}

void Overlay::upsert(const QJsonObject &o) {
    Item it;
    it.id = o.value("id").toString();
    if (it.id.isEmpty()) it.id = QString("_auto%1").arg(autoId_++);
    it.p = o;
    const int ttl = o.value("ttl").toInt(0);
    it.expires = ttl > 0 ? clock_.elapsed() + ttl : 0;
    for (Item &e : items_)
        if (e.id == it.id) { e = it; return; }
    items_.append(it);
}

void Overlay::handleMessage(const QJsonObject &m) {
    const QString cmd = m.value("cmd").toString("set");
    if (cmd == "clear") items_.clear();
    else if (cmd == "remove") {
        const QString id = m.value("id").toString();
        items_.erase(std::remove_if(items_.begin(), items_.end(),
                                    [&](const Item &i) { return i.id == id; }), items_.end());
    } else if (cmd == "screen") emit screenRequested(m.value("index").toInt());
    else if (cmd == "interactive") setInteractive(m.value("enabled").toBool(true));
    else if (cmd == "hide") visible_ = false;
    else if (cmd == "show") visible_ = true;
    else if (cmd == "batch") { for (auto v : m.value("items").toArray()) upsert(v.toObject()); }
    else upsert(m); // "set"
    update();
}

void Overlay::setInteractive(bool on) {
    if (on == interactive_) return;
    interactive_ = on;
    setWindowFlag(Qt::WindowTransparentForInput, !on); // hides the window; caller re-presents it
    setAttribute(Qt::WA_TransparentForMouseEvents, !on);
    setMouseTracking(on);
    emit interactiveChanged();
    update();
}

void Overlay::sendMouse(const char *type, QMouseEvent *e) {
    const char *btn = e->button() == Qt::LeftButton ? "left" : e->button() == Qt::RightButton ? "right"
                    : e->button() == Qt::MiddleButton ? "middle" : "none";
    emit mouseEvent({{"event", "mouse"}, {"type", type}, {"button", btn},
                     {"x", e->position().x() / width()}, {"y", e->position().y() / height()}});
}

static QColor col(const QJsonObject &o, const char *k, const QColor &def) {
    if (!o.contains(k)) return def;
    QColor c(o.value(k).toString());
    return c.isValid() ? c : def;
}

void Overlay::paintEvent(QPaintEvent *) {
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing);
    p.setRenderHint(QPainter::TextAntialiasing);
    p.setCompositionMode(QPainter::CompositionMode_Source);
    p.fillRect(rect(), QColor(0, 0, 0, interactive_ ? 1 : 0));
    p.setCompositionMode(QPainter::CompositionMode_SourceOver);
    if (!visible_) return;

    // All coordinates are fractions: x,w by width; y,h and all sizes by HEIGHT
    // (so circles stay round and text scales with resolution).
    const double W = width(), H = height();
    auto num = [](const QJsonObject &o, const char *k, double d) { return o.value(k).toDouble(d); };

    for (const Item &it : items_) {
        const QJsonObject &o = it.p;
        const QString type = o.value("type").toString("text");
        const QColor c = col(o, "color", Qt::white);
        QPen pen(c, std::max(1.0, num(o, "width", 0.004) * H), Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);

        if (type == "line") {
            QPointF a(num(o, "x1", 0) * W, num(o, "y1", 0) * H), b(num(o, "x2", 0) * W, num(o, "y2", 0) * H);
            p.setPen(pen);
            p.drawLine(a, b);
            if (o.value("arrow").toBool()) {
                const double ang = std::atan2(b.y() - a.y(), b.x() - a.x()), len = 0.025 * H;
                for (double s : {-0.45, 0.45})
                    p.drawLine(b, b - QPointF(std::cos(ang + s) * len, std::sin(ang + s) * len));
            }
        } else if (type == "rect" || type == "circle") {
            p.setPen(num(o, "width", 0.004) <= 0 ? Qt::NoPen : pen);
            p.setBrush(col(o, "fill", Qt::transparent));
            if (type == "rect") {
                QRectF r(num(o, "x", 0) * W, num(o, "y", 0) * H, num(o, "w", 0.1) * W, num(o, "h", 0.1) * H);
                const double rad = num(o, "radius", 0) * H;
                p.drawRoundedRect(r, rad, rad);
            } else {
                const double r = num(o, "r", 0.05) * H;
                p.drawEllipse(QPointF(num(o, "x", 0.5) * W, num(o, "y", 0.5) * H), r, r);
            }
            p.setBrush(Qt::NoBrush);
        } else { // text
            const QString text = o.value("text").toString();
            const QString align = o.value("align").toString("left");
            QFont f = p.font();
            f.setPixelSize(std::max(4, int(num(o, "size", 0.03) * H)));
            f.setBold(o.value("bold").toBool(false));
            p.setFont(f);
            const int al = align == "center" ? Qt::AlignHCenter : align == "right" ? Qt::AlignRight : Qt::AlignLeft;
            const QRectF sz = p.boundingRect(QRectF(0, 0, W, H), al | Qt::TextDontClip, text);
            double x = num(o, "x", 0) * W;
            if (align == "center") x -= sz.width() / 2;
            else if (align == "right") x -= sz.width();
            QRectF r(x, num(o, "y", 0) * H, sz.width(), sz.height());
            if (o.contains("bg")) {
                const double pad = 0.01 * H;
                p.setPen(Qt::NoPen);
                p.setBrush(col(o, "bg", QColor(0, 0, 0, 160)));
                p.drawRoundedRect(r.adjusted(-pad, -pad / 2, pad, pad / 2), pad, pad);
                p.setBrush(Qt::NoBrush);
            }
            if (o.value("shadow").toBool(true)) { // keeps text readable on any background
                p.setPen(QColor(0, 0, 0, 200));
                p.drawText(r.translated(0.002 * H, 0.002 * H), al | Qt::TextDontClip, text);
            }
            p.setPen(c);
            p.drawText(r, al | Qt::TextDontClip, text);
        }
    }
}
