#include "PlatformTweaks.h"
#include <QWidget>
#include <QWindow>
#include <cstdlib>

#ifdef HAVE_LAYERSHELL
#include <LayerShellQt/Window>
#endif

namespace PlatformTweaks {
void preApp() {
    // Avoid crashes in GTK/GNOME platform-theme plugins; an overlay needs no theming.
    if (!qEnvironmentVariableIsSet("QT_QPA_PLATFORMTHEME"))
        qputenv("QT_QPA_PLATFORMTHEME", "generic");
    if (!qEnvironmentVariableIsSet("QT_QPA_PLATFORM") && qEnvironmentVariableIsSet("WAYLAND_DISPLAY"))
        qputenv("QT_QPA_PLATFORM", "wayland");
}
void beforeShow(QWidget *w) {
#ifdef HAVE_LAYERSHELL
    // GNOME's compositor (Mutter) has no layer-shell; requesting it would break the window.
    if (qEnvironmentVariable("XDG_CURRENT_DESKTOP").contains("GNOME", Qt::CaseInsensitive)) {
        qWarning("GNOME detected: no layer-shell, overlay will not sit above fullscreen windows.");
        return;
    }
    if (auto *ls = LayerShellQt::Window::get(w->windowHandle())) {
        ls->setLayer(LayerShellQt::Window::LayerOverlay); // above fullscreen windows
        ls->setAnchors(LayerShellQt::Window::Anchors(
            LayerShellQt::Window::AnchorTop | LayerShellQt::Window::AnchorBottom |
            LayerShellQt::Window::AnchorLeft | LayerShellQt::Window::AnchorRight));
        ls->setExclusiveZone(-1); // don't reserve space, ignore other panels
        ls->setKeyboardInteractivity(LayerShellQt::Window::KeyboardInteractivityNone);
        ls->setScope("goi-overlay");
    }
#else
    Q_UNUSED(w);
#endif
}
void afterShow(QWidget *) {}
void keepOnTop(QWidget *) {}
}
