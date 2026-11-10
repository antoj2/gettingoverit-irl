#pragma once
class QWidget;
namespace PlatformTweaks {
void preApp();                  // before QApplication exists
void beforeShow(QWidget *w);    // after create(), before show()
void afterShow(QWidget *w);     // after show()
void keepOnTop(QWidget *w);     // called periodically
}
