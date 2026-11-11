#pragma once
#include <QElapsedTimer>
#include <QJsonObject>
#include <QList>
#include <QMouseEvent>
#include <QWidget>

struct Item {
    QString id;
    QJsonObject p;
    qint64 expires = 0; // ms on overlay clock, 0 = never
};

class Overlay : public QWidget {
    Q_OBJECT
public:
    explicit Overlay(QWidget *parent = nullptr);
    void handleMessage(const QJsonObject &m);
    bool interactive() const { return interactive_; }

signals:
    void screenRequested(int index);
    void interactiveChanged();            // window flags changed: caller must re-present the window
    void mouseEvent(const QJsonObject &); // only emitted while interactive

protected:
    void paintEvent(QPaintEvent *) override;
    void mousePressEvent(QMouseEvent *e) override { sendMouse("press", e); }
    void mouseReleaseEvent(QMouseEvent *e) override { sendMouse("release", e); }
    void mouseMoveEvent(QMouseEvent *e) override { sendMouse("move", e); }

private:
    void upsert(const QJsonObject &o);
    void setInteractive(bool on);
    void sendMouse(const char *type, QMouseEvent *e);
    bool interactive_ = false;
    QList<Item> items_;
    QElapsedTimer clock_;
    bool visible_ = true;
    int autoId_ = 0;
};
