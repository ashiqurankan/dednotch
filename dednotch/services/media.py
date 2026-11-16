"""Now-playing information and control through MPRIS (org.mpris.MediaPlayer2).

Any MPRIS-capable player (Spotify, browsers, Rhythmbox, VLC, mpv, ...) works.
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request

from gi.repository import Gio, GLib, GdkPixbuf, Gtk

from .. import gfx
from ..config import settings
from . import Observable

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PATH = "/org/mpris/MediaPlayer2"


def _unpack(v):
    return v.unpack() if isinstance(v, GLib.Variant) else v


class Player:
    def __init__(self, service, bus_name):
        self.service = service
        self.bus_name = bus_name
        self.last_active = 0.0
        self.props = {}
        self.root_props = {}
        self.proxy = None
        self.root = None
        self.position = 0.0
        self.position_stamp = time.monotonic()
        Gio.DBusProxy.new(service.bus, Gio.DBusProxyFlags.NONE, None, bus_name, PATH,
                          PLAYER_IFACE, None, self._on_proxy)
        Gio.DBusProxy.new(service.bus, Gio.DBusProxyFlags.NONE, None, bus_name, PATH,
                          ROOT_IFACE, None, self._on_root)

    # -- setup ------------------------------------------------------------
    def _on_proxy(self, _src, res):
        try:
            self.proxy = Gio.DBusProxy.new_finish(res)
        except GLib.Error:
            return
        for name in self.proxy.get_cached_property_names() or []:
            self.props[name] = _unpack(self.proxy.get_cached_property(name))
        self.proxy.connect("g-properties-changed", self._on_props_changed)
        self.proxy.connect("g-signal", self._on_signal)
        if self.props.get("PlaybackStatus") == "Playing":
            self.last_active = time.monotonic()
        self.refresh_position()
        self.service._player_changed(self, initial=True)

    def _on_root(self, _src, res):
        try:
            self.root = Gio.DBusProxy.new_finish(res)
        except GLib.Error:
            return
        for name in self.root.get_cached_property_names() or []:
            self.root_props[name] = _unpack(self.root.get_cached_property(name))
        self.service._player_changed(self)

    def _on_props_changed(self, _proxy, changed, _invalidated):
        changed = changed.unpack()
        status_before = self.props.get("PlaybackStatus")
        self.props.update(changed)
        if "PlaybackStatus" in changed:
            if changed["PlaybackStatus"] == "Playing":
                self.last_active = time.monotonic()
            elif status_before == "Playing":
                # freeze the estimated position at pause time
                self.position = self.estimated_position()
                self.position_stamp = time.monotonic()
        if "Metadata" in changed or "PlaybackStatus" in changed or "Rate" in changed:
            self.refresh_position()
        self.service._player_changed(self)

    def _on_signal(self, _proxy, _sender, signal, params):
        if signal == "Seeked":
            self.position = params.unpack()[0] / 1e6
            self.position_stamp = time.monotonic()
            self.service._player_changed(self)

    def refresh_position(self):
        if not self.proxy:
            return
        self.service.bus.call(self.bus_name, PATH, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (PLAYER_IFACE, "Position")),
                              GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 800, None,
                              self._on_position)

    def _on_position(self, bus, res):
        try:
            value = bus.call_finish(res).unpack()[0]
            self.position = max(0.0, float(value) / 1e6)
            self.position_stamp = time.monotonic()
            self.service._player_changed(self, position_only=True)
        except (GLib.Error, TypeError, ValueError):
            pass

    # -- state ------------------------------------------------------------
    @property
    def playing(self):
        return self.props.get("PlaybackStatus") == "Playing"

    @property
    def metadata(self):
        return self.props.get("Metadata") or {}

    @property
    def rate(self):
        try:
            return float(self.props.get("Rate", 1.0)) or 1.0
        except (TypeError, ValueError):
            return 1.0

    def estimated_position(self):
        if not self.playing:
            return self.position
        return self.position + (time.monotonic() - self.position_stamp) * self.rate

    # -- control ----------------------------------------------------------
    def call(self, method, params=None):
        if self.proxy:
            self.proxy.call(method, params, Gio.DBusCallFlags.NONE, 2000, None, None)

    def set_prop(self, name, variant):
        self.service.bus.call(self.bus_name, PATH, "org.freedesktop.DBus.Properties", "Set",
                              GLib.Variant("(ssv)", (PLAYER_IFACE, name, variant)),
                              None, Gio.DBusCallFlags.NONE, 2000, None, None)

    def identity(self):
        return self.root_props.get("Identity") or self.bus_name[len(MPRIS_PREFIX):].split(".")[0]

    def desktop_entry(self):
        return self.root_props.get("DesktopEntry") or ""


class MediaService(Observable):
    def __init__(self):
        super().__init__()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.players = {}
        self.active = None

        # Published state
        self.title = ""
        self.artist = ""
        self.album = ""
        self.art = None
        self.art_is_app_icon = False
        self.art_url = None
        self.avg_color = gfx.WHITE
        self.duration = 0.0
        self.playing = False
        self.idle = True
        self.shuffle = False
        self.repeat = "off"          # off | all | one
        self.volume = 0.5
        self.volume_supported = False
        self.can_seek = False
        self.app_icon = None
        self.identity = ""
        self.lyrics = ""
        self.synced_lyrics = []
        self.fetching_lyrics = False

        self._idle_source = None
        self._last_track_key = None
        self._lyrics_key = None
        self._art_token = 0

        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
                                  "NameOwnerChanged", "/org/freedesktop/DBus", None,
                                  Gio.DBusSignalFlags.NONE, self._on_name_owner_changed)
        self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "ListNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                      -1, None, self._on_list_names)
        settings.connect(self._on_setting)

    # -- discovery -----------------------------------------------------------
    def _on_list_names(self, bus, res):
        try:
            names = bus.call_finish(res).unpack()[0]
        except GLib.Error:
            return
        for name in names:
            if name.startswith(MPRIS_PREFIX):
                self.players[name] = Player(self, name)

    def _on_name_owner_changed(self, _bus, _sender, _path, _iface, _signal, params):
        name, old, new = params.unpack()
        if not name.startswith(MPRIS_PREFIX):
            return
        if new and not old:
            self.players[name] = Player(self, name)
        elif old and not new:
            self.players.pop(name, None)
            if self.active and self.active.bus_name == name:
                self.active = None
                self._select_active()
                self._sync()

    def _on_setting(self, key, _value):
        if key == "media_player":
            self._select_active()
            self._sync()
        elif key == "enable_lyrics":
            self._lyrics_key = None
            self._maybe_fetch_lyrics()

    def available_players(self):
        return sorted({(p.bus_name[len(MPRIS_PREFIX):].split(".")[0], p.identity())
                       for p in self.players.values()})

    def _select_active(self):
        pref = settings["media_player"]
        candidates = list(self.players.values())
        if pref and pref != "auto":
            preferred = [p for p in candidates if pref.lower() in p.bus_name.lower()]
            if preferred:
                candidates = preferred
        if not candidates:
            self.active = None
            return
        playing = [p for p in candidates if p.playing]
        if playing:
            self.active = max(playing, key=lambda p: p.last_active)
        elif self.active not in candidates:
            self.active = max(candidates, key=lambda p: (p.last_active, bool(p.metadata)))

    def _player_changed(self, player, initial=False, position_only=False):
        before = self.active
        if self.active is None or (player.playing and player is not self.active) or initial:
            self._select_active()
        if self.active is not player and before is self.active and not initial:
            return
        if position_only:
            self.emit("position")
            return
        self._sync()

    # -- state sync ----------------------------------------------------------
    def _sync(self):
        p = self.active
        if p is None:
            was_playing = self.playing
            self.title = self.artist = self.album = ""
            self.playing = False
            self.duration = 0.0
            self.art = None
            self.app_icon = None
            self.identity = ""
            self._last_track_key = None
            if was_playing:
                self._schedule_idle()
            else:
                self.idle = True
            self.emit("state")
            return

        md = p.metadata
        title = md.get("xesam:title") or ""
        artist = md.get("xesam:artist") or ""
        if isinstance(artist, (list, tuple)):
            artist = ", ".join(a for a in artist if a)
        album = md.get("xesam:album") or ""
        length = md.get("mpris:length") or 0
        try:
            self.duration = max(0.0, float(length) / 1e6)
        except (TypeError, ValueError):
            self.duration = 0.0

        track_key = (p.bus_name, title, artist, album)
        track_changed = track_key != self._last_track_key
        self._last_track_key = track_key
        self.title, self.artist, self.album = title, artist, album

        was_playing = self.playing
        self.playing = p.playing
        self.shuffle = bool(p.props.get("Shuffle", False))
        loop = p.props.get("LoopStatus", "None")
        self.repeat = {"None": "off", "Playlist": "all", "Track": "one"}.get(loop, "off")
        self.can_seek = bool(p.props.get("CanSeek", False))
        vol = p.props.get("Volume")
        self.volume_supported = vol is not None
        if vol is not None:
            try:
                self.volume = max(0.0, min(1.0, float(vol)))
            except (TypeError, ValueError):
                pass
        self.identity = p.identity()
        self._load_app_icon(p)

        art_url = md.get("mpris:artUrl") or ""
        if art_url != self.art_url or (self.art is None and not art_url):
            self.art_url = art_url
            self._load_art(art_url)

        if self.playing:
            self._cancel_idle()
            self.idle = False
        elif was_playing or not self.idle:
            self._schedule_idle()

        if track_changed and title:
            self._maybe_fetch_lyrics()
            if self.playing:
                self.emit("track-changed")
        if self.playing and not was_playing and not track_changed:
            self.emit("track-changed")
        self.emit("state")

    def _schedule_idle(self):
        self._cancel_idle()
        self._idle_source = GLib.timeout_add(int(settings["wait_interval"] * 1000), self._go_idle)

    def _cancel_idle(self):
        if self._idle_source:
            GLib.source_remove(self._idle_source)
            self._idle_source = None

    def _go_idle(self):
        self._idle_source = None
        self.idle = not self.playing
        self.emit("state")
        return False

    # -- artwork ---------------------------------------------------------------
    def _load_app_icon(self, player):
        entry = player.desktop_entry()
        key = entry or player.bus_name
        if getattr(self, "_app_icon_key", None) == key:
            return
        self._app_icon_key = key
        self.app_icon = None
        try:
            info = Gio.DesktopAppInfo.new(entry + ".desktop") if entry else None
            icon = info.get_icon() if info else None
            theme = Gtk.IconTheme.get_default()
            names = []
            if icon is not None:
                if isinstance(icon, Gio.FileIcon):
                    self.app_icon = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        icon.get_file().get_path(), 64, 64, True)
                    return
                names = list(icon.get_names()) if hasattr(icon, "get_names") else []
            names += [entry, player.identity().lower(), "multimedia-player"]
            for name in names:
                if name and theme.has_icon(name):
                    self.app_icon = theme.load_icon(name, 64, Gtk.IconLookupFlags.FORCE_SIZE)
                    return
        except Exception:
            self.app_icon = None

    def _load_art(self, url):
        self._art_token += 1
        token = self._art_token
        if not url:
            self._set_art(None, token)
            return
        if url.startswith("file://"):
            path = urllib.parse.unquote(url[7:])
            try:
                self._set_art(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 512, 512, True), token)
            except GLib.Error:
                self._set_art(None, token)
            return
        if url.startswith(("http://", "https://")):
            def worker():
                pb = None
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "DedNotch"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = resp.read(8 * 1024 * 1024)
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(data)
                    loader.close()
                    pb = loader.get_pixbuf()
                    if pb and max(pb.get_width(), pb.get_height()) > 512:
                        pb = pb.scale_simple(512, 512, GdkPixbuf.InterpType.BILINEAR)
                except Exception:
                    pb = None
                GLib.idle_add(self._set_art, pb, token)
            threading.Thread(target=worker, daemon=True).start()
            return
        self._set_art(None, token)

    def _set_art(self, pixbuf, token):
        if token != self._art_token:
            return False
        if pixbuf is None and self.app_icon is not None:
            self.art = self.app_icon
            self.art_is_app_icon = True
        else:
            self.art = pixbuf
            self.art_is_app_icon = False
        self.avg_color = gfx.average_color(self.art) if self.art is not None else gfx.WHITE
        self.emit("art")
        return False

    # -- lyrics (LRCLIB) --------------------------------------------------------
    def _maybe_fetch_lyrics(self):
        if not settings["enable_lyrics"] or not self.title:
            self.lyrics, self.synced_lyrics = "", []
            return
        key = (self.title, self.artist)
        if key == self._lyrics_key:
            return
        self._lyrics_key = key
        self.fetching_lyrics = True
        self.lyrics, self.synced_lyrics = "", []
        title, artist = self.title, self.artist

        def worker():
            plain, synced = "", []
            try:
                q = urllib.parse.urlencode({"track_name": _normalize(title),
                                            "artist_name": _normalize(artist)})
                req = urllib.request.Request("https://lrclib.net/api/search?" + q,
                                             headers={"User-Agent": "DedNotch"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    results = json.loads(resp.read().decode("utf-8"))
                if results:
                    first = results[0]
                    plain = (first.get("plainLyrics") or "").strip()
                    synced = _parse_lrc(first.get("syncedLyrics") or "")
            except Exception:
                pass
            GLib.idle_add(self._set_lyrics, key, plain, synced)

        threading.Thread(target=worker, daemon=True).start()

    def _set_lyrics(self, key, plain, synced):
        if key != self._lyrics_key:
            return False
        self.fetching_lyrics = False
        self.lyrics, self.synced_lyrics = plain, synced
        self.emit("state")
        return False

    def lyric_line(self, elapsed):
        if self.fetching_lyrics:
            return "Loading lyrics…"
        if self.synced_lyrics:
            line = ""
            for t, text in self.synced_lyrics:
                if t <= elapsed + 0.15:
                    line = text
                else:
                    break
            return line
        text = self.lyrics.strip()
        return text.replace("\n", " ") if text else "No lyrics found"

    # -- public API ----------------------------------------------------------------
    def position(self):
        if self.active is None:
            return 0.0
        pos = self.active.estimated_position()
        if self.duration > 0:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def force_update(self):
        if self.active:
            self.active.refresh_position()

    def play_pause(self):
        if self.active:
            self.active.call("PlayPause")

    def next(self):
        if self.active:
            self.active.call("Next")

    def previous(self):
        if self.active:
            self.active.call("Previous")

    def seek_to(self, seconds):
        p = self.active
        if not p:
            return
        track = p.metadata.get("mpris:trackid")
        us = int(max(0.0, seconds) * 1e6)
        if track and isinstance(track, str) and track.startswith("/"):
            p.call("SetPosition", GLib.Variant("(ox)", (track, us)))
        else:
            p.call("Seek", GLib.Variant("(x)", (us - int(p.estimated_position() * 1e6),)))
        p.position = seconds
        p.position_stamp = time.monotonic()
        self.emit("position")

    def skip(self, seconds):
        self.seek_to(max(0.0, self.position() + seconds))

    def toggle_shuffle(self):
        if self.active:
            self.active.set_prop("Shuffle", GLib.Variant("b", not self.shuffle))

    def toggle_repeat(self):
        if self.active:
            nxt = {"off": "Playlist", "all": "Track", "one": "None"}[self.repeat]
            self.active.set_prop("LoopStatus", GLib.Variant("s", nxt))

    def set_volume(self, value):
        if self.active:
            self.volume = max(0.0, min(1.0, value))
            self.active.set_prop("Volume", GLib.Variant("d", self.volume))

    def open_player(self):
        p = self.active
        if not p:
            return
        if p.root and p.root_props.get("CanRaise"):
            p.root.call("Raise", None, Gio.DBusCallFlags.NONE, 2000, None, None)
            return
        entry = p.desktop_entry()
        if entry:
            try:
                Gio.DesktopAppInfo.new(entry + ".desktop").launch([], None)
            except Exception:
                pass


def _normalize(text):
    text = re.sub(r"\s*[\(\[].*?[\)\]]", "", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_lrc(lrc):
    out = []
    for line in lrc.splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", line)
        text = re.sub(r"\[[^\]]*\]", "", line).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), text))
    out.sort(key=lambda x: x[0])
    return out
