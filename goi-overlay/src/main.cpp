#include "Overlay.h"
#include "PlatformTweaks.h"
#include <QApplication>
#include <QCommandLineParser>
#include <QJsonDocument>
#include <QNetworkDatagram>
#include <QScreen>
#include <QTimer>
#include <QUdpSocket>
#include <QWindow>

static const char *kDemo = R"({"cmd":"batch","items":[
 {"id":"t","type":"text","x":0.5,"y":0.06,"size":0.05,"align":"center","bold":true,"color":"#ffd400","text":"Raise your right arm"},
 {"id":"s","type":"text","x":0.5,"y":0.13,"size":0.025,"align":"center","bg":"#a0000000","text":"Overlay demo - clicks pass through to the game"},
 {"id":"a","type":"line","x1":0.5,"y1":0.5,"x2":0.7,"y2":0.3,"width":0.008,"color":"#ff3b30","arrow":true},
 {"id":"c","type":"circle","x":0.5,"y":0.5,"r":0.04,"color":"#30d158","fill":"#4030d158"},
 {"id":"r","type":"rect","x":0.02,"y":0.85,"w":0.25,"h":0.1,"radius":0.01,"color":"#ffffff","fill":"#80000000"},
 {"id":"rt","type":"text","x":0.03,"y":0.87,"size":0.03,"text":"PAUSED\nHold both arms up to resume"}
]})";

int main(int argc, char **argv) {
    PlatformTweaks::preApp();
    QApplication app(argc, argv);

    QCommandLineParser cli;
    cli.addHelpOption();
    QCommandLineOption port("port", "UDP port to listen on (localhost).", "port", "45454");
    QCommandLineOption evPort("event-port", "UDP port that mouse events are sent to.", "port", "45455");
    QCommandLineOption screenOpt("screen", "Screen index to start on (see list printed at startup).", "n", "0");
    QCommandLineOption rectOpt("rect", "Cover only x,y,w,h (not on Wayland).", "x,y,w,h");
    QCommandLineOption demo("demo", "Show a demo scene.");
    cli.addOptions({port, evPort, screenOpt, rectOpt, demo});
    cli.process(app);

    const auto screens = QGuiApplication::screens();
    for (int i = 0; i < screens.size(); ++i) {
        const QRect g = screens[i]->geometry();
        qInfo("screen %d: %s %dx%d+%d+%d", i, qPrintable(screens[i]->name()), g.width(), g.height(), g.x(), g.y());
    }
    QScreen *screen = screens.value(cli.value(screenOpt).toInt(), QGuiApplication::primaryScreen());
    QScreen *first = screen;
    QRect userRect;
    if (cli.isSet(rectOpt)) {
        const auto p = cli.value(rectOpt).split(',');
        if (p.size() == 4) userRect = QRect(p[0].toInt(), p[1].toInt(), p[2].toInt(), p[3].toInt());
    }

    Overlay w;
    auto present = [&](QScreen *s) {
        screen = s;
        w.hide();
        w.setGeometry(!userRect.isNull() && s == first ? userRect : s->geometry());
        w.winId(); // forces creation of the native window so windowHandle() is valid
        w.windowHandle()->setScreen(s);
        PlatformTweaks::beforeShow(&w);
        w.show();
        PlatformTweaks::afterShow(&w, w.interactive());
    };
    QObject::connect(&w, &Overlay::screenRequested, [&](int i) {
        const auto sc = QGuiApplication::screens();
        if (i >= 0 && i < sc.size()) present(sc[i]);
    });
    QObject::connect(&w, &Overlay::interactiveChanged, [&] { present(screen); });
    present(screen);

    QTimer keep;
    QObject::connect(&keep, &QTimer::timeout, [&] { PlatformTweaks::keepOnTop(&w); });
    keep.start(1000);

    QUdpSocket sock;
    if (!sock.bind(QHostAddress::LocalHost, cli.value(port).toUShort()))
        qWarning("Could not bind UDP port: %s", qPrintable(sock.errorString()));
    QObject::connect(&sock, &QUdpSocket::readyRead, [&] {
        while (sock.hasPendingDatagrams()) {
            const QByteArray data = sock.receiveDatagram().data();
            for (const QByteArray &line : data.split('\n')) { // one JSON object per line
                if (line.trimmed().isEmpty()) continue;
                const auto doc = QJsonDocument::fromJson(line);
                if (doc.isObject()) w.handleMessage(doc.object());
            }
        }
    });
    const quint16 outPort = cli.value(evPort).toUShort();
    QObject::connect(&w, &Overlay::mouseEvent, [&](const QJsonObject &o) {
        sock.writeDatagram(QJsonDocument(o).toJson(QJsonDocument::Compact) + "\n", QHostAddress::LocalHost, outPort);
    });

    if (cli.isSet(demo)) w.handleMessage(QJsonDocument::fromJson(kDemo).object());
    return app.exec();
}
