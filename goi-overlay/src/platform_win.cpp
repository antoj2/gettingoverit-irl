#include "PlatformTweaks.h"
#include <QWidget>
#include <windows.h>

namespace PlatformTweaks {
void preApp() {}
void beforeShow(QWidget *) {}
void afterShow(QWidget *w, bool interactive) {
    HWND h = reinterpret_cast<HWND>(w->winId());
    LONG_PTR ex = GetWindowLongPtr(h, GWL_EXSTYLE);
    ex |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST;
    if (interactive) ex &= ~WS_EX_TRANSPARENT; else ex |= WS_EX_TRANSPARENT;
    SetWindowLongPtr(h, GWL_EXSTYLE, ex);
    keepOnTop(w);
}
void keepOnTop(QWidget *w) {
    SetWindowPos(reinterpret_cast<HWND>(w->winId()), HWND_TOPMOST, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW);
}
}
