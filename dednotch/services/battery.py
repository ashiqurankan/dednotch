"""Battery state from UPower."""
from gi.repository import Gio, GLib

from . import Observable

UPOWER = "org.freedesktop.UPower"
DEVICE_IFACE = "org.freedesktop.UPower.Device"

# UPower device states
CHARGING, DISCHARGING, EMPTY, FULL, PENDING_CHARGE, PENDING_DISCHARGE = 1, 2, 3, 4, 5, 6


class BatteryService(Observable):
    def __init__(self):
        super().__init__()
        self.available = False
        self.level = 0.0
        self.charging = False
        self.plugged_in = False
        self.low_power = False
        self.max_capacity = 100.0
        self.time_to_full = 0          # minutes
        self.status_text = ""
        self._initialised = False
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

        try:
            self.root = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None, UPOWER,
                                               "/org/freedesktop/UPower", UPOWER, None)
            self.display = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None, UPOWER,
                                                  "/org/freedesktop/UPower/devices/DisplayDevice",
                                                  DEVICE_IFACE, None)
            self.root.connect("g-properties-changed", lambda *a: self._update())
            self.display.connect("g-properties-changed", lambda *a: self._update())
        except GLib.Error:
            self.root = self.display = None

        self.battery_dev = None
        try:
            paths = self.root.call_sync("EnumerateDevices", None, Gio.DBusCallFlags.NONE, 2000,
                                        None).unpack()[0] if self.root else []
            for path in paths:
                dev = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None, UPOWER,
                                             path, DEVICE_IFACE, None)
                t = dev.get_cached_property("Type")
                if t is not None and t.unpack() == 2 and dev.get_cached_property("PowerSupply").unpack():
                    self.battery_dev = dev
                    dev.connect("g-properties-changed", lambda *a: self._update())
                    break
        except GLib.Error:
            pass

        self.profiles = None
        for name, path in (("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
                           ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")):
            try:
                proxy = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None, name, path,
                                               name, None)
                if proxy.get_cached_property("ActiveProfile") is not None:
                    self.profiles = proxy
                    proxy.connect("g-properties-changed", lambda *a: self._update())
                    break
            except GLib.Error:
                continue
        self._update()

    def _prop(self, proxy, name, default):
        if proxy is None:
            return default
        v = proxy.get_cached_property(name)
        return v.unpack() if v is not None else default

    def _update(self):
        present = self._prop(self.display, "IsPresent", False)
        dev_type = self._prop(self.display, "Type", 0)
        self.available = bool(present) and dev_type == 2
        level = float(self._prop(self.display, "Percentage", 0.0))
        state = self._prop(self.display, "State", 0)
        on_battery = self._prop(self.root, "OnBattery", True)
        plugged = (not on_battery) and self.available
        charging = state in (CHARGING,) and plugged
        low_power = self._prop(self.profiles, "ActiveProfile", "") == "power-saver"
        ttf = int(self._prop(self.display, "TimeToFull", 0)) // 60
        if self.battery_dev is not None:
            cap = self._prop(self.battery_dev, "Capacity", 100.0)
            self.max_capacity = float(cap) if cap else 100.0

        events = []
        if self._initialised:
            if plugged != self.plugged_in:
                events.append("Plugged In" if plugged else "Unplugged")
            if low_power != self.low_power:
                events.append("Low Power: %s" % ("On" if low_power else "Off"))
            if charging != self.charging and plugged == self.plugged_in:
                events.append("Charging battery" if charging else
                              ("Not charging" if level < self.max_capacity else "Full charge"))
        else:
            self.status_text = "Plugged In" if plugged else "Unplugged"

        self.level, self.charging, self.plugged_in = level, charging, plugged
        self.low_power, self.time_to_full = low_power, ttf
        self._initialised = True
        if events:
            self.status_text = events[-1]
            self.emit("notify")
        self.emit("state")
