#include "PlatformTweaks.h"
#include <QWidget>
#import <AppKit/AppKit.h>

namespace PlatformTweaks {
void preApp() {}
void beforeShow(QWidget *) {}
void afterShow(QWidget *w, bool interactive) {
    // Accessory apps (no dock icon) are allowed onto other apps' fullscreen Spaces.
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    NSView *v = reinterpret_cast<NSView *>(w->winId());
    NSWindow *win = [v window];
    [win setLevel:NSScreenSaverWindowLevel];
    [win setCollectionBehavior:NSWindowCollectionBehaviorCanJoinAllSpaces |
                               NSWindowCollectionBehaviorFullScreenAuxiliary |
                               NSWindowCollectionBehaviorStationary |
                               NSWindowCollectionBehaviorIgnoresCycle];
    [win setIgnoresMouseEvents:interactive ? NO : YES];
    [win setHidesOnDeactivate:NO];
    [win setHasShadow:NO];
    [win setOpaque:NO];
    [win setBackgroundColor:[NSColor clearColor]];
    [win orderFrontRegardless];
}
void keepOnTop(QWidget *) {}
}
