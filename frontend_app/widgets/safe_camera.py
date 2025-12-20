from __future__ import annotations

from kivy.logger import Logger
from kivy.uix.camera import Camera


class AndroidSafeCamera(Camera):
    """
    A safer Camera widget for Android.

    Why this exists:
    Kivy's `Camera` eagerly initializes the Android camera service when `index`
    is set (even during KV rule application). On some devices/Android versions,
    this can throw a Java exception (e.g. "Fail to connect to camera service")
    which crashes the app at startup.

    Strategy:
    - Default `index` to -1 (do NOT connect during __init__/KV building).
    - When `play` becomes True, set `index = 0` and call super (wrapped).
    - When `play` becomes False, stop and set `index = -1`.
    """

    def __init__(self, **kwargs):
        # Ensure we don't initialize the camera during KV apply.
        if "index" not in kwargs:
            kwargs["index"] = -1
        super().__init__(**kwargs)

    def on_index(self, _instance, value):  # type: ignore[override]
        # Guard: ignore negative indexes (means "do not connect").
        try:
            if value is None or int(value) < 0:
                return
        except Exception:
            return

        # Normal behavior (may still fail, but we catch it to avoid app crash).
        try:
            return super().on_index(_instance, value)
        except Exception:
            Logger.exception("AndroidSafeCamera: failed to init camera (index=%s)", value)
            # Reset to "disconnected" state so app continues.
            try:
                self.index = -1
            except Exception:
                pass
            return None

    def on_play(self, _instance, value):  # type: ignore[override]
        # Kivy toggles play; connect/disconnect safely.
        try:
            playing = bool(value)
        except Exception:
            playing = False

        if playing:
            # Defer actual camera connection until play=True.
            try:
                if int(getattr(self, "index", -1) or -1) < 0:
                    self.index = 0
            except Exception:
                Logger.exception("AndroidSafeCamera: could not set index=0")
        else:
            # Disconnect camera to release service.
            try:
                super().on_play(_instance, value)
            except Exception:
                pass
            try:
                self.index = -1
            except Exception:
                pass

        try:
            return super().on_play(_instance, value)
        except Exception:
            Logger.exception("AndroidSafeCamera: failed toggling play=%s", value)
            try:
                self.index = -1
            except Exception:
                pass
            return None
