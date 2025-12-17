from __future__ import annotations

import os
import urllib.parse
from threading import Thread

from kivy.clock import Clock
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_video_match, api_video_end


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

    def on_enter(self, *args):
        """Start camera when entering the screen."""
        self._start_camera()

    def on_leave(self, *args):
        """Stop camera when leaving the screen."""
        self._stop_camera()

    def _start_camera(self):
        """Start the local camera preview."""
        camera = self.ids.get("local_camera")
        if camera:
            camera.play = True

    def _stop_camera(self):
        """Stop the local camera preview."""
        camera = self.ids.get("local_camera")
        if camera:
            camera.play = False

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
                    self.match_is_online = bool(match.get("is_online") or False)
                    
                    # Set image URL with fallback
                    raw_img = str(match.get("image_url") or "")
                    if raw_img.strip():
                        self.match_image_url = self._normalize_image_url(raw_img)
                    else:
                        self.match_image_url = self._fallback_avatar_url(
                            self.match_name or self.match_username or "User"
                        )

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

    def open_chat(self) -> None:
        if not self.manager or self.session_id <= 0:
            return
        chat = self.manager.get_screen("chat")
        chat.set_session(session_id=self.session_id, mode="text")
        self.manager.current = "chat"

    def go_back(self):
        self._stop_timer()
        
        # End call in backend to clear busy status
        def end_call_bg():
            try:
                api_video_end()
            except Exception:
                pass
        Thread(target=end_call_bg, daemon=True).start()

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

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """Normalize image URL to absolute URL."""
        u = (url or "").strip()
        if not u:
            return ""
        if "://" in u:
            return u
        base = os.getenv("BACKEND_URL", "https://dirt-0atr.onrender.com")
        base = (base or "").rstrip("/")
        if not u.startswith("/"):
            u = "/" + u
        return f"{base}{u}"

    @staticmethod
    def _fallback_avatar_url(name: str) -> str:
        """Generate a placeholder avatar URL."""
        n = (name or "User").strip() or "User"
        return "https://ui-avatars.com/api/?" + urllib.parse.urlencode(
            {
                "name": n,
                "background": "222222",
                "color": "ffffff",
                "size": "512",
                "bold": "true",
            }
        )
