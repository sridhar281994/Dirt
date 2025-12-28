from __future__ import annotations

from threading import Thread

from kivy.utils import platform
from kivy.properties import StringProperty

from frontend_app.screens.video_screen import VideoScreen
from frontend_app.utils.api import api_video_end
from frontend_app.utils.storage import get_user


class StartVideoDateScreen(VideoScreen):
    """
    WhatsApp-like random video date screen.

    Requirements implemented:
    - WhatsApp-style layout is defined in KV (<StartVideoDateScreen>).
    - Default matchmaking uses OPPOSITE gender (when known).
    - Connected match lasts 40 seconds, then auto-Next (timer is inherited).
    - End button is only shown when the remote user is connected (KV binding).
    """

    # Allow ChooseScreen to pass a preference, but we'll default to "opposite gender".
    preference = StringProperty("both")  # male|female|both

    @staticmethod
    def _default_opposite_preference() -> str:
        u = get_user() or {}
        me = str(u.get("gender") or "").strip().lower()
        if me == "male":
            return "female"
        if me == "female":
            return "male"
        return "both"

    def _effective_preference(self) -> str:
        p = (self.preference or "").strip().lower()
        if p in {"male", "female"}:
            return p
        return self._default_opposite_preference()

    def on_enter(self, *args):
        # Do NOT start chat polling on this screen (keeps UI lightweight, avoids layout storms).
        self._init_local_preview_transform()
        self._ensure_android_av_permissions()
        self.controls_visible = True
        self._refresh_use_agora()
        self._sync_remote_loading_state()

        # Start "video date" matching immediately.
        self.start_random(preference=self._effective_preference())

    def apply_match_payload(self, data: dict, *, preference: str = "both") -> None:
        super().apply_match_payload(data, preference=preference)
        # Force 40 seconds per your requirement (auto-next is handled by VideoScreen._tick()).
        self.duration_seconds = 40

    def toggle_camera(self) -> None:
        # If Agora is active, still keep UI state in sync so the button text updates.
        if platform == "android":
            try:
                if self._agora and self._agora.is_joined:
                    self._agora.switch_camera()
                    # Best-effort: track which cam we *think* is active for the label.
                    self.is_front_camera = not bool(self.is_front_camera)
                    self.local_preview_scale_x = -1 if bool(self.is_front_camera) else 1
                    return
            except Exception:
                pass
        return super().toggle_camera()

    def next_call(self) -> None:
        # End backend call to clear busy status before matching next.
        sid = int(self.session_id or 0)
        if sid > 0:
            Thread(target=lambda: api_video_end(session_id=sid), daemon=True).start()
        # Then request the next match.
        self.start_random(preference=self._effective_preference())
