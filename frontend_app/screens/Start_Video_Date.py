from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.screenmanager import Screen
from kivy.utils import platform

from frontend_app.utils.android_camera import get_android_camera_ids
from frontend_app.utils.api import ApiError, api_video_match


class StartVideoDateScreen(Screen):
    """
    Dedicated screen for "Start Video Date" so UI + logic are isolated.

    Responsibilities:
    - Show local camera preview
    - Show loading spinner while searching for an online user
    - Keep retrying when no user is online
    - Match randomly based on gender preference and then route to VideoScreen
    """

    # Matching / UI state
    preference = StringProperty("both")
    show_loading = BooleanProperty(True)
    status_text = StringProperty("Searching for online users...")
    _spin_ev = None
    _retry_ev = None
    _inflight = BooleanProperty(False)

    # Camera
    camera_permission_granted = BooleanProperty(False)
    audio_permission_granted = BooleanProperty(False)
    camera_should_play = BooleanProperty(True)

    active_camera_index = NumericProperty(0)
    back_camera_index = NumericProperty(0)
    front_camera_index = NumericProperty(1)
    is_front_camera = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._init_camera_ids()

    def _init_camera_ids(self) -> None:
        try:
            ids = get_android_camera_ids()
            self.back_camera_index = int(ids.back)
            self.front_camera_index = int(ids.front)
            # Always default to BACK camera for this entry screen.
            self.active_camera_index = int(ids.back)
        except Exception:
            self.back_camera_index = 0
            self.front_camera_index = 1
            self.active_camera_index = 0
        self._update_front_flag()

    def _update_front_flag(self) -> None:
        self.is_front_camera = int(self.active_camera_index or 0) == int(self.front_camera_index or 1)

    def on_pre_enter(self, *args):
        self._init_camera_ids()
        self._refresh_android_permission_state()

    def on_enter(self, *args):
        self._ensure_android_av_permissions()
        # Auto-start search when entering (preference may be set by ChooseScreen).
        self.retry()

    def on_leave(self, *args):
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()

    # ---------- Camera (minimal, safe) ----------

    def _refresh_android_permission_state(self) -> None:
        if platform != "android":
            self.camera_permission_granted = True
            self.audio_permission_granted = True
            return

        try:
            from android.permissions import Permission, check_permission  # type: ignore

            self.camera_permission_granted = bool(check_permission(Permission.CAMERA))
            self.audio_permission_granted = bool(check_permission(Permission.RECORD_AUDIO))
        except Exception:
            self.camera_permission_granted = False
            self.audio_permission_granted = False

    def _ensure_android_av_permissions(self) -> None:
        self._refresh_android_permission_state()

        if bool(self.camera_permission_granted) and bool(self.audio_permission_granted):
            self._start_camera()
            return

        if platform != "android":
            self._start_camera()
            return

        try:
            from android.permissions import Permission, request_permissions  # type: ignore

            perms = [Permission.CAMERA, Permission.RECORD_AUDIO]

            def _cb(_permissions, _grants):
                def _apply(*_):
                    self._refresh_android_permission_state()
                    if self.camera_permission_granted:
                        self._start_camera()
                Clock.schedule_once(_apply, 0)

            request_permissions(perms, _cb)
        except Exception:
            Logger.exception("StartVideoDateScreen: permission request failed")

    def _start_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if not cam:
            return
        try:
            # Ensure camera index is set before play is True.
            if hasattr(cam, "index"):
                cam.index = int(self.active_camera_index or 0)
        except Exception:
            Logger.exception("StartVideoDateScreen: failed to set camera index")
        self.camera_should_play = True

    def _stop_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if cam:
            try:
                self.camera_should_play = False
            except Exception:
                pass
            try:
                if hasattr(cam, "index"):
                    cam.index = -2
            except Exception:
                pass

    def toggle_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if not cam:
            return

        try:
            was_playing = bool(self.camera_should_play)
            self.camera_should_play = False

            back = int(self.back_camera_index or 0)
            front = int(self.front_camera_index or 1)
            current = int(self.active_camera_index or back)
            self.active_camera_index = front if current == back else back
            self._update_front_flag()

            if hasattr(cam, "index"):
                cam.index = int(self.active_camera_index)

            if was_playing:
                Clock.schedule_once(lambda *_: setattr(self, "camera_should_play", True), 0.25)
        except Exception:
            Logger.exception("StartVideoDateScreen: failed to toggle camera")
            self.camera_should_play = True

    # ---------- Loading spinner ----------

    def _start_spinner(self) -> None:
        if self._spin_ev is None:
            self._spin_ev = Clock.schedule_interval(self._spin, 1 / 30.0)

    def _stop_spinner(self) -> None:
        if self._spin_ev is not None:
            try:
                self._spin_ev.cancel()
            except Exception:
                pass
            self._spin_ev = None
        try:
            sp = self.ids.get("loading_spinner")
            if sp is not None:
                sp.rotation = 0
        except Exception:
            pass

    def _spin(self, _dt):
        try:
            sp = self.ids.get("loading_spinner")
            if sp is not None:
                sp.rotation = (float(getattr(sp, "rotation", 0.0)) + 10.0) % 360.0
        except Exception:
            pass

    # ---------- Match / retry loop ----------

    def start_search(self, *, preference: str) -> None:
        self.preference = (preference or "both").strip().lower() or "both"
        self.status_text = "Searching for online users..."
        self.show_loading = True
        self._start_spinner()
        self._cancel_retry()
        self._request_match_once()

    def retry(self) -> None:
        # Called by UI (Retry button) and on_enter().
        self.start_search(preference=self.preference or "both")

    def _cancel_retry(self) -> None:
        if self._retry_ev is not None:
            try:
                self._retry_ev.cancel()
            except Exception:
                pass
            self._retry_ev = None

    def _schedule_retry(self, *, delay: float = 2.0) -> None:
        self._cancel_retry()
        self._retry_ev = Clock.schedule_once(lambda *_: self._request_match_once(), float(delay))

    def _request_match_once(self) -> None:
        if bool(self._inflight):
            return
        self._inflight = True

        pref = (self.preference or "both").strip().lower() or "both"

        def work():
            try:
                data = api_video_match(preference=pref)
                match = (data or {}).get("match") or {}
                has_match = bool(match.get("username")) and bool(match.get("is_online") or False)

                def apply_ok(*_):
                    self._inflight = False
                    if not has_match:
                        # No online users (or match not available yet): keep loading + retry.
                        self.status_text = "No users online. Searching..."
                        self.show_loading = True
                        self._start_spinner()
                        self._schedule_retry(delay=2.0)
                        return

                    # We have an online match → route into VideoScreen using the payload.
                    self.show_loading = False
                    self._stop_spinner()
                    self.status_text = "Connecting..."

                    if not self.manager:
                        return
                    video = self.manager.get_screen("video")
                    video.apply_match_payload(data, preference=pref)
                    self.manager.current = "video"

                Clock.schedule_once(apply_ok, 0)
            except ApiError as exc:
                msg = str(exc or "")

                def apply_err(*_):
                    self._inflight = False
                    # Treat "no users online" as non-fatal and keep retrying.
                    low = msg.lower()
                    if "no online" in low or "no users" in low or "not available" in low:
                        self.status_text = "No users online. Searching..."
                        self.show_loading = True
                        self._start_spinner()
                        self._schedule_retry(delay=2.5)
                        return

                    # Other errors: show message but keep trying (backend might be temporarily down).
                    self.status_text = msg or "Network error. Retrying..."
                    self.show_loading = True
                    self._start_spinner()
                    self._schedule_retry(delay=3.5)

                Clock.schedule_once(apply_err, 0)
            except Exception:
                def apply_crash(*_):
                    self._inflight = False
                    self.status_text = "Unexpected error. Retrying..."
                    self.show_loading = True
                    self._start_spinner()
                    self._schedule_retry(delay=3.5)

                Clock.schedule_once(apply_crash, 0)

        Thread(target=work, daemon=True).start()

    def go_back(self) -> None:
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()
        if self.manager:
            self.manager.current = "choose"

