#!/usr/bin/env python3

import os
import re
import signal

import dbus
import dbus.mainloop.glib

import gi
gi.require_version("Gst", "1.0")

from gi.repository import GLib, Gst


dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
Gst.init(None)


PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"


bus = dbus.SessionBus()
portal = bus.get_object(PORTAL_BUS, PORTAL_PATH)

loop = GLib.MainLoop()

sender_name = re.sub(
    r"\.",
    "_",
    bus.get_unique_name()[1:]
)

request_counter = 0
session_counter = 0

session = None
pipeline = None

frame_count = 0
first_frame_saved = False


def new_request_path():
    global request_counter

    request_counter += 1

    token = f"u{request_counter}"

    path = (
        "/org/freedesktop/portal/desktop/request/"
        f"{sender_name}/{token}"
    )

    return path, token


def new_session_token():
    global session_counter

    session_counter += 1

    return f"u{session_counter}"


def portal_call(method, callback, *args, options=None):
    if options is None:
        options = {}

    request_path, request_token = new_request_path()

    bus.add_signal_receiver(
        callback,
        signal_name="Response",
        dbus_interface=REQUEST_IFACE,
        bus_name=PORTAL_BUS,
        path=request_path,
    )

    options["handle_token"] = request_token

    method(
        *args,
        dbus.Dictionary(options, signature="sv"),
        dbus_interface=SCREENCAST_IFACE,
    )


# ------------------------------------------------------------
# GStreamer
# ------------------------------------------------------------

def on_new_sample(appsink):
    global frame_count
    global first_frame_saved

    sample = appsink.emit("pull-sample")

    if sample is None:
        return Gst.FlowReturn.ERROR

    frame_count += 1

    if frame_count % 60 == 0:
        print(f"Received {frame_count} frames")

    if not first_frame_saved:

        first_frame_saved = True

        caps = sample.get_caps()
        structure = caps.get_structure(0)

        width = structure.get_value("width")
        height = structure.get_value("height")
        fmt = structure.get_value("format")

        print()
        print("=" * 60)
        print("FIRST FRAME RECEIVED")
        print("=" * 60)

        print("Width :", width)
        print("Height:", height)
        print("Format:", fmt)

        buffer = sample.get_buffer()

        success, map_info = buffer.map(Gst.MapFlags.READ)

        if success:

            try:

                data = bytes(map_info.data)

                print("Buffer bytes:", len(data))

                # We requested RGB below, so this can be written
                # directly as a PPM image.
                filename = "wayland_capture.ppm"

                with open(filename, "wb") as f:

                    f.write(
                        f"P6\n{width} {height}\n255\n".encode()
                    )

                    f.write(data)

                print()
                print("Saved:", filename)

                print()
                print("Open it with:")
                print("  xdg-open wayland_capture.ppm")

            finally:

                buffer.unmap(map_info)

    return Gst.FlowReturn.OK


def on_gst_error(bus_obj, message):

    error, debug = message.parse_error()

    print()
    print("=" * 60)
    print("GSTREAMER ERROR")
    print("=" * 60)

    print(error)

    if debug:
        print()
        print("Debug:")
        print(debug)

    stop()


def on_gst_eos(bus_obj, message):

    print("GStreamer EOS")
    stop()


def start_gstreamer(node_id, fd):

    global pipeline

    print()
    print("=" * 60)
    print("STARTING GSTREAMER")
    print("=" * 60)

    print("PipeWire FD:", fd)
    print("PipeWire node:", node_id)

    pipeline_description = (
        f"pipewiresrc "
        f"fd={fd} "
        f"path={node_id} "
        f"do-timestamp=true "
        f"! videoconvert "
        f"! video/x-raw,format=RGB "
        f"! appsink "
        f"name=sink "
        f"emit-signals=true "
        f"sync=false "
        f"max-buffers=1 "
        f"drop=true"
    )

    print()
    print("Pipeline:")
    print(pipeline_description)
    print()

    try:

        pipeline = Gst.parse_launch(
            pipeline_description
        )

    except Exception as e:

        print("Failed to create pipeline:")
        print(e)

        stop()

        return

    appsink = pipeline.get_by_name("sink")

    appsink.connect(
        "new-sample",
        on_new_sample
    )

    gst_bus = pipeline.get_bus()

    gst_bus.add_signal_watch()

    gst_bus.connect(
        "message::error",
        on_gst_error
    )

    gst_bus.connect(
        "message::eos",
        on_gst_eos
    )

    result = pipeline.set_state(
        Gst.State.PLAYING
    )

    print("Pipeline state result:", result)

    if result == Gst.StateChangeReturn.FAILURE:

        print("Failed to start pipeline.")

        stop()

        return

    print()
    print("GStreamer is running.")
    print("Waiting for frames...")


# ------------------------------------------------------------
# PipeWire
# ------------------------------------------------------------

def open_pipewire_response():

    print()
    print("=" * 60)
    print("OPENING PIPEWIRE REMOTE")
    print("=" * 60)

    try:

        fd_object = portal.OpenPipeWireRemote(
            session,
            dbus.Dictionary(
                {},
                signature="sv"
            ),
            dbus_interface=SCREENCAST_IFACE,
        )

    except Exception as e:

        print("OpenPipeWireRemote failed:")
        print(e)

        stop()

        return

    # dbus.types.UnixFd -> actual integer FD
    fd = fd_object.take()

    print("Received PipeWire FD:", fd)

    # We know this from Start().
    node_id = selected_node_id

    start_gstreamer(
        node_id,
        fd
    )


# ------------------------------------------------------------
# Portal responses
# ------------------------------------------------------------

def start_response(response, results):

    global selected_node_id

    print()
    print("=" * 60)
    print("START RESPONSE")
    print("=" * 60)

    print("Response:", response)

    if response != 0:

        print("Start failed/cancelled.")

        print("Results:", results)

        stop()

        return

    streams = results.get(
        "streams",
        []
    )

    print("Streams:", len(streams))

    if not streams:

        print("ERROR: no PipeWire streams returned.")

        stop()

        return

    node_id, properties = streams[0]

    selected_node_id = int(node_id)

    print("Node ID:", selected_node_id)

    for key, value in properties.items():

        print(
            f"{key}: {value}"
        )

    # This is the important next step.
    open_pipewire_response()


def select_sources_response(response, results):

    print()
    print("=" * 60)
    print("SELECT SOURCES RESPONSE")
    print("=" * 60)

    print("Response:", response)

    if response != 0:

        print("SelectSources failed.")

        print("Results:", results)

        stop()

        return

    print("Source selection accepted.")

    print()
    print("Starting ScreenCast...")

    portal_call(
        portal.Start,
        start_response,

        session,

        "",

        options={},
    )


def create_session_response(response, results):

    global session

    print()
    print("=" * 60)
    print("CREATE SESSION RESPONSE")
    print("=" * 60)

    print("Response:", response)

    if response != 0:

        print("CreateSession failed.")

        print("Results:", results)

        stop()

        return

    session = results[
        "session_handle"
    ]

    print("Session:", session)

    print()
    print("Selecting monitor/window...")

    portal_call(
        portal.SelectSources,
        select_sources_response,

        session,

        options={
            "types": dbus.UInt32(1 | 2),
            "multiple": dbus.Boolean(False),

            # Hidden cursor is preferable for the eventual
            # CV detector. We don't actually need the cursor
            # in the captured image.
            "cursor_mode": dbus.UInt32(1),
        },
    )


# ------------------------------------------------------------
# Shutdown
# ------------------------------------------------------------

def stop():

    global pipeline

    if pipeline is not None:

        pipeline.set_state(
            Gst.State.NULL
        )

        pipeline = None

    if loop.is_running():

        loop.quit()


def signal_handler(signum, frame):

    print()
    print("Stopping...")

    stop()


signal.signal(
    signal.SIGINT,
    signal_handler
)

signal.signal(
    signal.SIGTERM,
    signal_handler
)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

print("=" * 60)
print("Wayland Portal -> PipeWire -> GStreamer")
print("=" * 60)
print()

print(
    "D-Bus sender:",
    bus.get_unique_name()
)

print()

session = None
selected_node_id = None

session_token = new_session_token()

print("Creating ScreenCast session...")

portal_call(
    portal.CreateSession,
    create_session_response,

    options={
        "session_handle_token": session_token,
    },
)


try:

    loop.run()

finally:

    stop()


print()
print("Frames received:", frame_count)
print("Done.")