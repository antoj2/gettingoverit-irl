#!/usr/bin/env python3

import re
import dbus
import dbus.mainloop.glib

import gi
gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"

bus = dbus.SessionBus()
loop = GLib.MainLoop()

portal = bus.get_object(PORTAL_BUS, PORTAL_PATH)

request_counter = 0
session_counter = 0

sender_name = re.sub(
    r"\.",
    "_",
    bus.get_unique_name()[1:]
)


def new_request_path():
    global request_counter

    request_counter += 1

    token = f"u{request_counter}"

    path = (
        "/org/freedesktop/portal/desktop/request/"
        f"{sender_name}/{token}"
    )

    return path, token


def new_session_path():
    global session_counter

    session_counter += 1

    token = f"u{session_counter}"

    path = (
        "/org/freedesktop/portal/desktop/session/"
        f"{sender_name}/{token}"
    )

    return path, token


def portal_call(method, callback, *args, options=None):
    if options is None:
        options = {}

    request_path, request_token = new_request_path()

    print("Request path:")
    print(request_path)

    # IMPORTANT:
    # Register the Response listener BEFORE making the D-Bus call.
    bus.add_signal_receiver(
        callback,
        signal_name="Response",
        dbus_interface=REQUEST_IFACE,
        bus_name=PORTAL_BUS,
        path=request_path,
    )

    options["handle_token"] = request_token

    print("Calling portal method...")

    method(
        *args,
        dbus.Dictionary(options, signature="sv"),
        dbus_interface=SCREENCAST_IFACE,
    )

    print("D-Bus method returned.")


def create_session_response(response, results):
    print()
    print("=" * 60)
    print("CREATE SESSION RESPONSE")
    print("=" * 60)
    print("Response:", response)
    print("Results:", results)

    if response != 0:
        print("CreateSession failed.")
        loop.quit()
        return

    session = results.get("session_handle")

    print()
    print("SUCCESS!")
    print("Session:", session)
    print()
    print("The ScreenCast portal is responding correctly.")

    loop.quit()


def create_session():
    session_path, session_token = new_session_path()

    print("Expected session path:")
    print(session_path)
    print()

    portal_call(
        portal.CreateSession,
        create_session_response,
        options={
            "session_handle_token": session_token,
        },
    )


print("=" * 60)
print("Wayland Portal D-Bus diagnostic")
print("=" * 60)
print()

print("D-Bus sender:", bus.get_unique_name())
print()

create_session()

try:
    loop.run()
except KeyboardInterrupt:
    print("\nInterrupted.")