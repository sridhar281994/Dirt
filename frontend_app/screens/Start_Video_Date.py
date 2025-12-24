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
    preference = StringProperty("both")
    show_loading = BooleanProperty(True)
    status_text = StringProperty("Searching for online users...")
    _spin_ev = None
    _retry_ev = None
    _inflight = BooleanProperty(False)

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
            self.active_camera_index = int(ids.back)
        except Exception:
            self.back_camera_index = 0
            self.front_camera_index = 1
            self.active_camera_index = 0
        self._update_front_flag()

    def _update_front_flag(self) -> None:
        self.is_front_camera = int(self.active_camera_index) == int(self.front_camera_index)

    def on_pre_enter(self, *args):
        self._init_camera_ids()
        self._refresh_android_permission_state()

    def on_enter(self, *args):
        self._ensure_android_av_permissions()
        self.retry()

    def on_leave(self, *args):
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()

    def _refresh_android_permission_state(self) -> None:
        if platform != "android":
            self.camera_permission_granted = True
            self.audio_permission_granted = True
            return

        try:
            from android.permissions import Permission, check_permission
            self.camera_permission_granted = check_permission(Permission.CAMERA)
            self.audio_permission_granted = check_permission(Permission.RECORD_AUDIO)
        except Exception:
            self.camera_permission_granted = False
            self.audio_permission_granted = False

    def _ensure_android_av_permissions(self) -> None:
        self._refresh_android_permission_state()

        if self.camera_permission_granted and self.audio_permission_granted:
            self._start_camera()
            return

        if platform != "android":
            self._start_camera()
            return

        try:
            from android.permissions import Permission, request_permissions
            request_permissions(
                [Permission.CAMERA, Permission.RECORD_AUDIO],
                lambda *_: Clock.schedule_once(lambda __: self._start_camera(), 0),
            )
        except Exception:
            Logger.exception("permission request failed")

    def _start_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if cam and hasattr(cam, "index"):
            cam.index = int(self.active_camera_index)
        self.camera_should_play = True

    def _stop_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if cam:
            self.camera_should_play = False
            if hasattr(cam, "index"):
                cam.index = -2

    def toggle_camera(self) -> None:
        cam = self.ids.get("local_camera")
        if not cam:
            return

        was_playing = self.camera_should_play
        self.camera_should_play = False

        back = int(self.back_camera_index)
        front = int(self.front_camera_index)
        self.active_camera_index = front if self.active_camera_index == back else back
        self._update_front_flag()

        if hasattr(cam, "index"):
            cam.index = self.active_camera_index

        if was_playing:
            Clock.schedule_once(lambda *_: setattr(self, "camera_should_play", True), 0.25)

    def _start_spinner(self) -> None:
        if self._spin_ev is None:
            self._spin_ev = Clock.schedule_interval(self._spin, 1 / 30)

    def _stop_spinner(self) -> None:
        if self._spin_ev:
            self._spin_ev.cancel()
            self._spin_ev = None

    def _spin(self, _dt):
        sp = self.ids.get("loading_spinner")
        if sp:
            sp.rotation = (sp.rotation + 10) % 360

    def start_search(self, *, preference: str) -> None:
        self.preference = preference
        self.status_text = "Searching for online users..."
        self.show_loading = True
        self._start_spinner()
        self._cancel_retry()
        self._request_match_once()

    def retry(self) -> None:
        self.start_search(preference=self.preference)

    def _cancel_retry(self) -> None:
        if self._retry_ev:
            self._retry_ev.cancel()
            self._retry_ev = None

    def _schedule_retry(self, delay=2.0) -> None:
        self._cancel_retry()
        self._retry_ev = Clock.schedule_once(lambda *_: self._request_match_once(), delay)

    def _request_match_once(self) -> None:
        if self._inflight:
            return
        self._inflight = True

        def work():
            try:
                data = api_video_match(preference=self.preference)
                match = data.get("match") or {}
                has_match = match.get("is_online")

                def apply():
                    self._inflight = False
                    if not has_match:
                        self._schedule_retry()
                        return
                    self.manager.get_screen("video").apply_match_payload(data)
                    self.manager.current = "video"

                Clock.schedule_once(lambda *_: apply(), 0)
            except ApiError:
                Clock.schedule_once(lambda *_: self._schedule_retry(3.0), 0)

        Thread(target=work, daemon=True).start()

    def go_back(self) -> None:
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()
        if self.manager:
            self.manager.current = "choose"
