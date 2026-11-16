"""Background services feeding the notch UI (all run on the GLib main loop)."""


class Observable:
    def __init__(self):
        self._observers = []

    def connect(self, callback):
        self._observers.append(callback)

    def emit(self, *args):
        for cb in list(self._observers):
            try:
                cb(*args)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                print("dednotch: observer failed:", exc)
