from __future__ import annotations

import os
import urllib.parse
from threading import Thread

from kivy.clock import Clock
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.screenmanager import Screen
from kivy.utils import platform
from kivy.logger import Logger

from frontend_app.utils.api import ApiError, api_video_match, api_video_end, api_get_messages, api_post_message


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
    match_user_id = NumericProperty(0) # Store ID for reporting

    duration_seconds = NumericProperty(0)
    remaining_seconds = NumericProperty(0)

    controls_visible = BooleanProperty(True)
    camera_permission_granted = BooleanProperty(False)
    audio_permission_granted = BooleanProperty(False)
    is_muted = BooleanProperty(False)
    
    _ticker = None
    _chat_ticker = None
    last_preference = StringProperty("both")

    def on_pre_enter(self, *args):
        # Refresh permission flags whenever screen is about to show.
        self._refresh_android_permission_state()

    def on_enter(self, *args):
        """Ensure permissions and start camera when entering the screen."""
        self._ensure_android_av_permissions()
        self.controls_visible = True
        self._start_chat_polling()

    def on_leave(self, *args):
        """Stop camera when leaving the screen."""
        self._stop_camera()
        self._stop_chat_polling()

    def on_session_id(self, _instance, value):  # type: ignore[override]
        """
        Start/stop local preview when session becomes active.
        This also covers the random-match flow where session_id is set later.
        """
        try:
            sid = int(value or 0)
        except Exception:
            sid = 0

        if sid > 0:
            self._ensure_android_av_permissions()
        else:
            self._stop_camera()

    def _start_camera(self):
        """Start the local camera preview."""
        # Only start if session is active and permission is granted.
        if int(self.session_id or 0) <= 0:
            return
        if platform == "android" and not bool(self.camera_permission_granted):
            return

        camera = self.ids.get("local_camera")
        if camera:
            try:
                # Ensure index triggers only when we're ready (AndroidSafeCamera uses -1 as disconnected).
                if hasattr(camera, "index") and int(getattr(camera, "index", -1) or -1) < 0:
                    camera.index = 0
                camera.play = True
            except Exception:
                Logger.exception("Failed to start camera preview")

    def _stop_camera(self):
        """Stop the local camera preview."""
        camera = self.ids.get("local_camera")
        if camera:
            try:
                camera.play = False
            except Exception:
                pass
            try:
                if hasattr(camera, "index"):
                    camera.index = -1
            except Exception:
                pass

    def _refresh_android_permission_state(self) -> None:
        if platform != "android":
            # Non-Android: assume permission is available.
            self.camera_permission_granted = True
            self.audio_permission_granted = True
            return

        try:
            from android.permissions import Permission, check_permission  # type: ignore

            self.camera_permission_granted = bool(check_permission(Permission.CAMERA))
            self.audio_permission_granted = bool(check_permission(Permission.RECORD_AUDIO))
        except Exception:
            # If we cannot check, be conservative and treat as not granted.
            self.camera_permission_granted = False
            self.audio_permission_granted = False

    def _ensure_android_av_permissions(self) -> None:
        """
        Request camera + mic permissions if needed.

        Critical: start camera only AFTER the permission callback confirms grants.
        """
        self._refresh_android_permission_state()

        # If already granted (or not Android), just start.
        if bool(self.camera_permission_granted) and bool(self.audio_permission_granted):
            self._start_camera()
            return

        if platform != "android":
            self._start_camera()
            return

        try:
            from android.permissions import Permission, request_permissions  # type: ignore

            perms = [Permission.CAMERA, Permission.RECORD_AUDIO]

            def _cb(permissions, grants):
                # This callback may be invoked off the main thread; use Clock to touch UI.
                def _apply(*_):
                    try:
                        # Refresh from system, don't trust raw grants format.
                        self._refresh_android_permission_state()
                        if self.camera_permission_granted and self.audio_permission_granted:
                            self._start_camera()
                        else:
                            Logger.warning(
                                "VideoScreen: permissions denied. camera=%s audio=%s",
                                self.camera_permission_granted,
                                self.audio_permission_granted,
                            )
                    except Exception:
                        Logger.exception("VideoScreen: failed handling permission result")

                Clock.schedule_once(_apply, 0)

            request_permissions(perms, _cb)
        except Exception:
            Logger.exception("VideoScreen: permission request failed")

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
                    self.match_user_id = int(match.get("id") or 0)
                    
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
                    # If permissions are already granted, start local preview immediately.
                    self._ensure_android_av_permissions()

                Clock.schedule_once(apply, 0)
            except ApiError as exc:
                # Keep UI simple: show the error in the screen label via properties.
                error_msg = str(exc)
                def apply_err(*_):
                    self.session_id = 0
                    self.channel = ""
                    self.agora_app_id = ""
                    self.match_name = ""
                    self.match_username = ""
                    self.match_country = ""
                    self.match_desc = error_msg
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
        # Chat is now an overlay on this screen.
        # Ensure controls are visible so chat is visible.
        self.controls_visible = True
        pass

    def report_user(self):
        if self.match_user_id > 0:
            from frontend_app.utils.report_popup import show_report_popup
            show_report_popup(reported_user_id=self.match_user_id, context="video")
        else:
            # Generic report if no user matched yet? Or ignore
            pass

    def toggle_controls(self):
        self.controls_visible = not self.controls_visible

    def _start_chat_polling(self):
        self._stop_chat_polling()
        self._chat_ticker = Clock.schedule_interval(self._poll_chat, 2.0)
        # Initial poll
        self._poll_chat(0)

    def _stop_chat_polling(self):
        if self._chat_ticker:
            self._chat_ticker.cancel()
            self._chat_ticker = None

    def _poll_chat(self, _dt):
        if self.session_id <= 0:
            return
        
        def work():
            try:
                data = api_get_messages(session_id=self.session_id)
                msgs = data.get("messages") or []
                
                def update_ui(*_):
                    box = self.ids.get("chat_box")
                    if not box:
                        return
                    
                    # Simple optimization: check if count changed or just clear/redraw
                    # For a robust app, we'd diff. For now, clear/redraw is fine for small chats.
                    # But clear_widgets is expensive.
                    # Let's just clear and redraw for now to ensure correctness.
                    box.clear_widgets()
                    
                    from kivy.uix.label import Label
                    for m in msgs:
                        # sender = "Me" if int(m.get('sender_id') or 0) == ... else "Partner"
                        # We don't have easy access to my user ID here without get_user()
                        # But we can just show message content.
                        msg_text = str(m.get("message") or "")
                        lbl = Label(
                            text=msg_text,
                            size_hint_y=None,
                            height=30,
                            halign="left",
                            valign="middle",
                            text_size=(box.width, None),
                            color=(1, 1, 1, 1)
                        )
                        lbl.bind(texture_size=lbl.setter('size'))
                        # Force height update
                        def resize(instance, value):
                            instance.height = value[1]
                        lbl.bind(texture_size=resize)
                        
                        box.add_widget(lbl)
                        
                Clock.schedule_once(update_ui, 0)
            except Exception:
                pass

        Thread(target=work, daemon=True).start()

    def send_message(self):
        sid = int(self.session_id or 0)
        inp = self.ids.get("chat_input")
        if not inp or sid <= 0:
            return
            
        msg = (inp.text or "").strip()
        if not msg:
            return
            
        inp.text = "" # Clear immediately
        
        def work():
            try:
                api_post_message(session_id=sid, message=msg)
                # Force poll
                Clock.schedule_once(self._poll_chat, 0.1)
            except Exception:
                pass
        
        Thread(target=work, daemon=True).start()

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

    def toggle_mute(self) -> None:
        """
        Toggle local microphone mute (Android).

        Note: This does not implement a full WebRTC/Agora pipeline; it only mutes
        the device mic input using Android's AudioManager so the UI has a working
        mute switch.
        """
        self.is_muted = not bool(self.is_muted)

        if platform != "android":
            return

        try:
            from jnius import autoclass, cast  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            context = PythonActivity.mActivity
            AudioManager = autoclass("android.media.AudioManager")
            service = context.getSystemService(context.AUDIO_SERVICE)
            am = cast("android.media.AudioManager", service)
            am.setMicrophoneMute(bool(self.is_muted))
        except Exception:
            Logger.exception("VideoScreen: failed to toggle microphone mute")

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
