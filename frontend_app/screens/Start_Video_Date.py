from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.utils import platform
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

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
    _perm_popup = None
    _perm_popup_shown = False

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

    def _dismiss_perm_popup(self) -> None:
        p = getattr(self, "_perm_popup", None)
        if p is not None:
            try:
                p.dismiss()
            except Exception:
                pass
        self._perm_popup = None

    def _show_permissions_popup(self) -> None:
        """
        Show an in-app rationale popup, then trigger the Android system permission dialog.
        """
        if platform != "android":
            return
        if self._perm_popup is not None:
            return
        if bool(getattr(self, "_perm_popup_shown", False)):
            # Avoid spamming; user can re-enter screen to try again.
            return
        self._perm_popup_shown = True

        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(
            Label(
                text="Allow Camera & Microphone to start video date.",
                halign="center",
                valign="middle",
            )
        )
        btns = BoxLayout(size_hint_y=None, height=44, spacing=10)

        allow_btn = Button(text="Allow", background_color=(0.2, 0.8, 0.2, 1))
        cancel_btn = Button(text="Not now", background_color=(0.3, 0.3, 0.3, 1))
        btns.add_widget(cancel_btn)
        btns.add_widget(allow_btn)
        content.add_widget(btns)

        popup = Popup(title="Permissions required", content=content, size_hint=(0.85, 0.35), auto_dismiss=True)
        self._perm_popup = popup

        def _request(*_a):
            self._dismiss_perm_popup()
            self._request_android_av_permissions()

        def _cancel(*_a):
            self._dismiss_perm_popup()
            # Keep screen idle; user stays here.
            self.show_loading = False

        allow_btn.bind(on_release=_request)
        cancel_btn.bind(on_release=_cancel)
        popup.open()

    def _request_android_av_permissions(self) -> None:
        """
        Request CAMERA + RECORD_AUDIO using the native Android popup.
        After grant, start matching.
        """
        if platform != "android":
            self.start_random(preference=self._effective_preference())
            return
        try:
            from android.permissions import Permission, request_permissions, check_permission  # type: ignore
        except Exception:
            # If permissions API isn't available, just attempt to proceed.
            self.start_random(preference=self._effective_preference())
            return

        perms = [Permission.CAMERA, Permission.RECORD_AUDIO]

        def _cb(_permissions, _grants):
            def _apply(_dt):
                try:
                    cam_ok = bool(check_permission(Permission.CAMERA))
                    mic_ok = bool(check_permission(Permission.RECORD_AUDIO))
                    # Keep VideoScreen flags in sync.
                    self.camera_permission_granted = cam_ok
                    self.audio_permission_granted = mic_ok
                    if cam_ok and mic_ok:
                        self.start_random(preference=self._effective_preference())
                    else:
                        self.show_loading = False
                except Exception:
                    self.show_loading = False

            Clock.schedule_once(_apply, 0)

        try:
            request_permissions(perms, _cb)
        except Exception:
            self.start_random(preference=self._effective_preference())

    def on_enter(self, *args):
        # Do NOT start chat polling on this screen (keeps UI lightweight, avoids layout storms).
        self._init_local_preview_transform()
        self.controls_visible = True
        self._refresh_use_agora()
        self._sync_remote_loading_state()

        # Permissions:
        # - show a friendly popup first (then trigger native Android permission dialog)
        # - only start matching once granted
        self._ensure_android_av_permissions()
        if platform == "android" and not (bool(self.camera_permission_granted) and bool(self.audio_permission_granted)):
            self.show_loading = False
            Clock.schedule_once(lambda *_: self._show_permissions_popup(), 0)
            return

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
