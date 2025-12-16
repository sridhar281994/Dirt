from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_video_match


class VideoScreen(Screen):
    session_id = NumericProperty(0)
    channel = StringProperty("")
    agora_app_id = StringProperty("")

    match_name = StringProperty("")
    match_username = StringProperty("")
    match_country = StringProperty("")
    match_desc = StringProperty("")
    match_image_url = StringProperty("")
    match_is_online = BooleanProperty(False)

    duration_seconds = NumericProperty(0)
    remaining_seconds = NumericProperty(0)

    _ticker = None
    last_preference = StringProperty("both")

    def set_session(self, *, session_id: int):
        self.session_id = int(session_id)

    def start_random(self, *, preference: str = "both") -> None:
        # Cancel any existing countdown
        self._stop_timer()
        self.last_preference = (preference or "both").strip().lower() or "both"

        def work():
            try:
                data = api_video_match(preference=self.last_preference)
                sess = (data or {}).get("session") or {}
                match = (data or {}).get("match") or {}
                duration = int((data or {}).get("duration_seconds") or 40)

                def apply(*_):
                    self.session_id = int(sess.get("id") or 0)
                    self.channel = str((data or {}).get("channel") or "")
                    self.agora_app_id = str((data or {}).get("agora_app_id") or "")

                    self.match_name = str(match.get("name") or "")
                    self.match_username = str(match.get("username") or "")
                    self.match_country = str(match.get("country") or "")
                    self.match_desc = str(match.get("description") or "")
                    self.match_image_url = str(match.get("image_url") or "")
                    self.match_is_online = bool(match.get("is_online") or False)

                    self.duration_seconds = duration
                    self.remaining_seconds = duration
                    self._start_timer()

                Clock.schedule_once(apply, 0)
            except ApiError as exc:
                # Keep UI simple: show the error in the screen label via properties.
                def apply_err(*_):
                    self.session_id = 0
                    self.channel = ""
                    self.agora_app_id = ""
                    self.match_name = ""
                    self.match_username = ""
                    self.match_country = ""
                    self.match_desc = str(exc)
                    self.match_image_url = ""
                    self.match_is_online = False
                    self.duration_seconds = 0
                    self.remaining_seconds = 0
                    self._stop_timer()

                Clock.schedule_once(apply_err, 0)

        Thread(target=work, daemon=True).start()

    def next_call(self) -> None:
        # Uses the last chosen preference from ChooseScreen via start_random argument;
        # if user presses NEXT inside the video screen, we just request another random match.
        self.start_random(preference=self.last_preference)

    def go_back(self):
        self._stop_timer()
        if self.manager:
            self.manager.current = "choose"

    def _start_timer(self) -> None:
        self._stop_timer()
        self._ticker = Clock.schedule_interval(self._tick, 1.0)

    def _stop_timer(self) -> None:
        if self._ticker is not None:
            try:
                self._ticker.cancel()
            except Exception:
                pass
        self._ticker = None

    def _tick(self, _dt):
        rem = int(self.remaining_seconds or 0)
        if rem <= 0:
            self.remaining_seconds = 0
            self._stop_timer()
            # Auto-change to next call when timer expires
            self.next_call()
            return False
        self.remaining_seconds = rem - 1
        if self.remaining_seconds <= 0:
            self.remaining_seconds = 0
            self._stop_timer()
            # Auto-change to next call when timer expires
            self.next_call()
            return False
        return True

