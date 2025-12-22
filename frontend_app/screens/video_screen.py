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
    # Kivy's Android camera texture is often rotated 90deg vs portrait UI.
    # We compensate in KV by rotating the preview container.
    local_preview_rotation = NumericProperty(0)
    # Mirror (selfie) preview on X axis: 1 (normal) / -1 (mirrored)
    local_preview_scale_x = NumericProperty(1)

    # Drive Camera.play via KV binding so we can safely pause during switches.
    camera_should_play = BooleanProperty(True)
    active_camera_index = NumericProperty(0)  # 0=back, 1=front

    # Remote connection/loading state
    is_remote_connected = BooleanProperty(False)
    show_loading = BooleanProperty(False)
    
    _ticker = None
    _chat_ticker = None
    _loading_spinner = None
    last_preference = StringProperty("both")

    def on_pre_enter(self, *args):
        # Refresh permission flags whenever screen is about to show.
        self._refresh_android_permission_state()

    def on_enter(self, *args):
        """Ensure permissions and start camera when entering the screen."""
        self._init_local_preview_transform()
        self._ensure_android_av_permissions()
        self.controls_visible = True
        self._sync_remote_loading_state()
        self._start_chat_polling()

    def on_leave(self, *args):
        """Stop camera when leaving the screen."""
        self._stop_camera()
        self._set_loading(False)
        self._stop_chat_polling()

    def _init_local_preview_transform(self) -> None:
        """
        Initialize any platform-specific preview transforms.

        On many Android devices, the camera preview arrives in landscape
        orientation and needs a 90° correction to match a portrait UI.
        """
        self._update_local_preview_transform()

    def _update_local_preview_transform(self) -> None:
        """
        Update preview rotation/mirroring based on active camera.

        Android front/back cameras frequently report different sensor rotations.
        Empirically, many devices need:
        - back camera: -90
        - front camera: +90 (i.e., -90 + 180) and mirrored.
        """
        if platform == "android":
            is_front = int(self.active_camera_index or 0) == 1
            self.local_preview_rotation = 90 if is_front else -90
            self.local_preview_scale_x = -1 if is_front else 1
        else:
            # Desktop/iOS: default to no rotation; mirror front camera if used.
            is_front = int(self.active_camera_index or 0) == 1
            self.local_preview_rotation = 0
            self.local_preview_scale_x = -1 if is_front else 1

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
            # Keep local preview active even if call is not connected yet.
            # (User wants to see their own video while searching / after ending.)
            self._ensure_android_av_permissions()

    def _start_camera(self):
        """Start the local camera preview."""
        # Start whenever we're on the video screen and permission is granted.
        if platform == "android" and not bool(self.camera_permission_granted):
            return

        camera = self.ids.get("local_camera")
        if camera:
            try:
                # Ensure index triggers only when we're ready (AndroidSafeCamera uses -1 as disconnected).
                if hasattr(camera, "index") and int(getattr(camera, "index", -1) or -1) < 0:
                    camera.index = int(self.active_camera_index or 0)
                self.camera_should_play = True
            except Exception:
                Logger.exception("Failed to start camera preview")

    def _stop_camera(self):
        """Stop the local camera preview."""
        camera = self.ids.get("local_camera")
        if camera:
            try:
                self.camera_should_play = False
            except Exception:
                pass
            try:
                if hasattr(camera, "index"):
                    # AndroidSafeCamera uses <0 as "disconnected"; keep it consistent.
                    camera.index = -2
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
        # Show loader while we match/connect.
        self._set_loading(True)
        self.is_remote_connected = False

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
                    # Remote "connected" when we actually have an online match.
                    self._sync_remote_loading_state()

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
                    self.is_remote_connected = False
                    self._set_loading(False)

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

    def on_touch_down(self, touch):  # type: ignore[override]
        """
        Toggle overlays when tapping the video background.

        We avoid placing a full-screen Button overlay (it can render unexpectedly on
        some devices and can interfere with camera/video visibility).
        """
        try:
            if not self.collide_point(*touch.pos):
                return super().on_touch_down(touch)

            # If the user is interacting with UI overlays, do not toggle.
            for wid in ("top_bar", "controls_overlay", "chat_overlay", "local_preview"):
                w = self.ids.get(wid)
                if w is not None and w.collide_point(*touch.pos):
                    return super().on_touch_down(touch)

            # Background tap: toggle controls.
            self.toggle_controls()
            return True
        except Exception:
            return super().on_touch_down(touch)

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
        self._set_loading(False)
        
        # End call in backend to clear busy status
        def end_call_bg():
            try:
                api_video_end()
            except Exception:
                pass
        Thread(target=end_call_bg, daemon=True).start()

        if self.manager:
            self.manager.current = "choose"

    def end_call(self) -> None:
        """
        End/disconnect the current call WITHOUT leaving the video screen.
        """
        self._stop_timer()

        # Clear session + remote UI state (keep local preview running).
        self.session_id = 0
        self.channel = ""
        self.agora_app_id = ""
        self.match_name = ""
        self.match_username = ""
        self.match_country = ""
        self.match_desc = "Call ended"
        self.match_image_url = ""
        self.match_is_online = False
        self.match_user_id = 0
        self.duration_seconds = 0
        self.remaining_seconds = 0
        self.is_remote_connected = False
        self._set_loading(False)

        def end_call_bg():
            try:
                api_video_end()
            except Exception:
                pass

        Thread(target=end_call_bg, daemon=True).start()

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

    def toggle_camera(self) -> None:
        """Switch between front and back camera."""
        camera = self.ids.get("local_camera")
        if not camera:
            return

        # Give user feedback (quick spin animation on the flip icon).
        try:
            from kivy.animation import Animation

            icon = self.ids.get("flip_icon")
            if icon is not None:
                try:
                    icon.rotation = 0
                except Exception:
                    pass
                Animation(rotation=360, duration=0.25).start(icon)
        except Exception:
            pass
            
        try:
            # Pause via bound property (avoids fighting KV bindings).
            was_playing = bool(self.camera_should_play)
            self.camera_should_play = False

            # Flip using our own state (AndroidSafeCamera can set index to -2 while switching).
            current = int(self.active_camera_index or 0)
            new_index = 0 if current == 1 else 1
            self.active_camera_index = new_index
            self._update_local_preview_transform()

            camera.index = int(new_index)

            if was_playing:
                Clock.schedule_once(lambda *_: setattr(self, "camera_should_play", True), 0.25)
        except Exception:
            Logger.exception("VideoScreen: failed to toggle camera")
            # Best-effort: resume preview if we paused it.
            try:
                self.camera_should_play = True
            except Exception:
                pass

    def _sync_remote_loading_state(self) -> None:
        """
        Decide whether to show loader based on match/online state.
        """
        connected = bool(self.session_id > 0 and self.match_username and self.match_is_online)
        self.is_remote_connected = connected
        self._set_loading(not connected and bool(self.session_id > 0))

    def _set_loading(self, should_show: bool) -> None:
        should_show = bool(should_show)
        self.show_loading = should_show
        if should_show:
            if self._loading_spinner is None:
                self._loading_spinner = Clock.schedule_interval(self._spin_loading, 1 / 30.0)
        else:
            if self._loading_spinner is not None:
                try:
                    self._loading_spinner.cancel()
                except Exception:
                    pass
                self._loading_spinner = None
            # Reset rotation so it doesn't jump when shown again.
            try:
                spinner = self.ids.get("loading_spinner")
                if spinner is not None:
                    spinner.rotation = 0
            except Exception:
                pass

    def _spin_loading(self, _dt):
        try:
            spinner = self.ids.get("loading_spinner")
            if spinner is None:
                return
            spinner.rotation = (float(getattr(spinner, "rotation", 0.0)) + 10.0) % 360.0
        except Exception:
            pass

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
        # Use http to avoid SSL verification failures in some Windows/corporate setups.
        # This is only used for a non-sensitive placeholder avatar.
        return "http://ui-avatars.com/api/?" + urllib.parse.urlencode(
            {
                "name": n,
                "background": "222222",
                "color": "ffffff",
                "size": "512",
                "bold": "true",
            }
        )
