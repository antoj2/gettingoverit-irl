#pragma once
#include <QElapsedTimer>
#include <QJsonObject>
#include <QList>
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

protected:
    void paintEvent(QPaintEvent *) override;

private:
    void upsert(const QJsonObject &o);
    QList<Item> items_;
    QElapsedTimer clock_;
    bool visible_ = true;
    int autoId_ = 0;
};
