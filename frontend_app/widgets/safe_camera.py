from __future__ import annotations

from kivy.clock import Clock
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
            # NOTE:
            # Kivy's Camera.__init__ contains:
            #   if self.index == -1: self.index = 0
            # which eagerly initializes the camera service.
            # Use any value < -1 to avoid that eager path during KV build.
            kwargs["index"] = -2
        super().__init__(**kwargs)
        # Track failures to avoid infinite fallback loops.
        self._failed_indices: set[int] = set()
        self._retry_counts: dict[int, int] = {}
        self._last_working_index: int | None = None
        self._switch_scheduled = False

    def _switch_to(self, target: int, *, delay: float = 0.35) -> None:
        """
        Switch camera index on the next frame.

        Important: Changing `index` inside `_on_index` can recurse; scheduling avoids re-entrancy.
        """
        if self._switch_scheduled:
            return
        self._switch_scheduled = True

        def _do(_dt):
            self._switch_scheduled = False
            try:
                self.index = int(target)
            except Exception:
                # Give up: disconnected state.
                try:
                    self.index = -2
                except Exception:
                    pass

        Clock.schedule_once(_do, float(delay or 0))

    def _on_index(self, *largs):  # type: ignore[override]
        """
        Wrap Kivy's Camera._on_index with crash-safety and a small fallback strategy.

        Why:
        - Kivy's Camera binds `index` to `_on_index` directly (not `on_index`).
        - If CoreCamera initialization throws (common on some Android devices),
          it can crash the app unless we catch it here.
        """
        try:
            # Fast-path: don't connect on negative indices.
            try:
                if int(getattr(self, "index", -1) or -1) < 0:
                    return
            except Exception:
                return

            ret = super()._on_index(*largs)
            try:
                self._last_working_index = int(getattr(self, "index", 0) or 0)
            except Exception:
                self._last_working_index = None
            return ret
        except Exception:
            Logger.exception("AndroidSafeCamera: failed to init camera (index=%s)", getattr(self, "index", None))

            try:
                failed = int(getattr(self, "index", -999999) or -999999)
            except Exception:
                failed = -999999
            self._failed_indices.add(failed)

            # Heuristic recovery:
            # - If non-zero index failed, fall back to 0.
            # - If 0 failed, retry once (some backends are flaky on first open).
            if failed >= 1 and 0 not in self._failed_indices:
                Logger.warning("AndroidSafeCamera: falling back to index=0 (failed index=%s)", failed)
                self._switch_to(0, delay=0.35)
                return

            if failed == 0:
                c = int(self._retry_counts.get(0, 0))
                if c < 1:
                    self._retry_counts[0] = c + 1
                    Logger.warning("AndroidSafeCamera: retrying index=0 once after failure")
                    self._switch_to(0, delay=0.6)
                    return

            # Reset to a safe disconnected state so app continues.
            try:
                self.index = -2
            except Exception:
                pass
            try:
                self.play = False
            except Exception:
                pass
            return

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
                    # Prefer last working index if we have one; otherwise default to 0.
                    self.index = int(self._last_working_index if self._last_working_index is not None else 0)
            except Exception:
                Logger.exception("AndroidSafeCamera: could not set index=0")

            try:
                # Ensure CoreCamera exists before starting (index might already be set).
                try:
                    if getattr(self, "_camera", None) is None and hasattr(self, "_on_index"):
                        self._on_index()
                except Exception:
                    pass
                return super().on_play(_instance, value)
            except Exception:
                Logger.exception("AndroidSafeCamera: failed starting play=%s", value)
                # Reset to a safe disconnected state.
                try:
                    self.index = -2
                except Exception:
                    pass
                try:
                    self.play = False
                except Exception:
                    pass
                return None

        # playing == False: stop first, then disconnect the underlying camera service.
        ret = None
        try:
            ret = super().on_play(_instance, value)
        except Exception:
            pass
        try:
            self.index = -2
        except Exception:
            pass
        return ret
