"""Webcam mirror ("Ded mirror") through GStreamer v4l2src."""
import glob

import cairo
import gi

from . import Observable

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
except (ValueError, ImportError):
    Gst = None

from gi.repository import GLib  # noqa: E402

W, H = 320, 240


class WebcamService(Observable):
    def __init__(self):
        super().__init__()
        self.pipeline = None
        self.frame = None
        self.running = False

    @property
    def available(self):
        return Gst is not None and bool(glob.glob("/dev/video*"))

    def start(self):
        if self.running or not self.available:
            return
        try:
            self.pipeline = Gst.parse_launch(
                "v4l2src ! videoconvert ! videoscale ! videoflip method=horizontal-flip ! "
                "video/x-raw,format=BGRx,width=%d,height=%d,pixel-aspect-ratio=1/1 ! "
                "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false" % (W, H))
        except GLib.Error as exc:
            print("dednotch: camera unavailable:", exc)
            self.pipeline = None
            return
        sink = self.pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)
        self.pipeline.set_state(Gst.State.PLAYING)
        self.running = True
        self.emit("state")

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self.running = False
        self.frame = None
        self.emit("state")

    def _on_error(self, _bus, msg):
        err, _dbg = msg.parse_error()
        print("dednotch: camera error:", err.message)
        GLib.idle_add(self.stop)

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytearray(info.data)
            buf.unmap(info)
            GLib.idle_add(self._set_frame, data)
        return Gst.FlowReturn.OK

    def _set_frame(self, data):
        if not self.running:
            return False
        try:
            self.frame = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_RGB24, W, H, W * 4)
            self._frame_data = data
        except Exception:
            self.frame = None
        self.emit("frame")
        return False
