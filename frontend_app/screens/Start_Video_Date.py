from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.metrics import dp
from kivy.uix.screenmanager import Screen
from kivy.utils import platform

from frontend_app.utils.android_camera import get_android_camera_ids
from frontend_app.utils.api import ApiError, api_get_messages, api_post_message, api_video_end, api_video_match
from frontend_app.utils.storage import get_user


class StartVideoDateScreen(Screen):
    preference = StringProperty("both")
    show_loading = BooleanProperty(True)
    status_text = StringProperty("Searching for online users...")
    _spin_ev = None
    _retry_ev = None
    _inflight = BooleanProperty(False)
    _pending_next = BooleanProperty(False)

    camera_permission_granted = BooleanProperty(False)
    audio_permission_granted = BooleanProperty(False)
    camera_should_play = BooleanProperty(True)

    active_camera_index = NumericProperty(0)
    back_camera_index = NumericProperty(0)
    front_camera_index = NumericProperty(1)
    is_front_camera = BooleanProperty(False)

    # Local preview transform (rotate + mirror/flip) for correct device orientation.
    local_preview_rotation = NumericProperty(0)
    local_preview_scale_x = NumericProperty(1)
    local_preview_scale_y = NumericProperty(1)
    local_preview_swap_wh = BooleanProperty(False)

    # Microphone mute toggle (UI + Android AudioManager).
    is_muted = BooleanProperty(False)

    # Public chat overlay (small, last 5 messages).
    _chat_ticker = None
    # Active session chat (1:1) once matched.
    session_id = NumericProperty(0)
    match_user_id = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._init_camera_ids()
        self._init_local_preview_transform()

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
        self._update_local_preview_transform()

    def _init_local_preview_transform(self) -> None:
        self._update_local_preview_transform()

    def _update_local_preview_transform(self) -> None:
        """
        Fix camera preview orientation.

        User requirement:
        - The preview must NOT be rotated (upright, "same as live").
        - Front camera may be mirrored (selfie-style), but no rotation.
        """
        # No rotation anywhere.
        self.local_preview_rotation = 0
        self.local_preview_scale_y = 1

        try:
            self.local_preview_swap_wh = int(abs(float(self.local_preview_rotation)) % 180) == 90
        except Exception:
            self.local_preview_swap_wh = False

        # Mirror selfie preview on X for front camera (optional; keeps appearance correct)
        self.local_preview_scale_x = -1 if bool(self.is_front_camera) else 1

    def on_pre_enter(self, *args):
        self._init_camera_ids()
        self._refresh_android_permission_state()

    def on_enter(self, *args):
        self._ensure_android_av_permissions()
        self._start_chat_polling()
        self.retry()

    def on_leave(self, *args):
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()
        self._stop_chat_polling()
        # Reset ephemeral in-call state for next entry.
        self._reset_session_chat()

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

    def toggle_mute(self) -> None:
        """
        Toggle local microphone mute (Android).

        This mirrors `VideoScreen.toggle_mute()` so users can quickly mute/unmute
        while waiting for a match.
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
            Logger.exception("StartVideoDateScreen: failed to toggle microphone mute")

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
                sess = data.get("session") or {}
                new_sid = int(sess.get("id") or 0)
                new_match_user_id = int(match.get("id") or 0)

                def apply():
                    self._inflight = False
                    # If user pressed NEXT while this request was inflight, discard this match
                    # (but clean up backend busy flags) and immediately request another.
                    if self._pending_next:
                        self._pending_next = False
                        if new_sid > 0:
                            self._end_backend_video_call(session_id=new_sid)
                        self.status_text = "Searching for next online user..."
                        self.show_loading = True
                        self._start_spinner()
                        Clock.schedule_once(lambda *_: self._request_match_once(), 0)
                        return

                    if not has_match:
                        if self._pending_next:
                            # NEXT requested: retry immediately.
                            self._pending_next = False
                            Clock.schedule_once(lambda *_: self._request_match_once(), 0)
                        else:
                            self._schedule_retry()
                        return
                    # Update local session-chat context before switching screens
                    # (best-effort; if user stays on this screen for any reason,
                    # chat becomes 1:1 instead of global).
                    self._set_session_chat(session_id=new_sid, match_user_id=new_match_user_id)
                    self.manager.get_screen("video").apply_match_payload(data)
                    self.manager.current = "video"

                Clock.schedule_once(lambda *_: apply(), 0)
            except ApiError:
                def apply_err():
                    self._inflight = False
                    # If NEXT was requested while inflight, try again quickly.
                    if self._pending_next:
                        self._pending_next = False
                        Clock.schedule_once(lambda *_: self._request_match_once(), 0)
                        return
                    self._schedule_retry(3.0)

                Clock.schedule_once(lambda *_: apply_err(), 0)

        Thread(target=work, daemon=True).start()

    def _end_backend_video_call(self, *, session_id: int | None = None) -> None:
        def work():
            try:
                api_video_end(session_id=session_id)
            except Exception:
                pass

        Thread(target=work, daemon=True).start()

    def next_call(self) -> None:
        """
        Skip to the next available online user.
        """
        # If a match request is currently inflight, mark NEXT and let the response
        # clean up and re-request safely (avoids concurrent /video/match calls).
        if self._inflight:
            self._pending_next = True
            self.status_text = "Searching for next online user..."
            self.show_loading = True
            self._start_spinner()
            self._cancel_retry()
            return

        # End any current session (if present), then request a new match.
        sid = int(self.session_id or 0)
        if sid > 0:
            self._end_backend_video_call(session_id=sid)
        self._reset_session_chat()

        self.status_text = "Searching for next online user..."
        self.show_loading = True
        self._start_spinner()
        self._cancel_retry()
        self._request_match_once()

    def end_call(self) -> None:
        """
        End/disconnect the current call (only meaningful if someone is connected).
        """
        sid = int(self.session_id or 0)
        self._cancel_retry()
        self._stop_spinner()
        if sid > 0:
            self._end_backend_video_call(session_id=sid)
        self._reset_session_chat()
        self.show_loading = False
        self.status_text = "Call ended"

    def _start_chat_polling(self) -> None:
        self._stop_chat_polling()
        # Poll 1:1 session chat lightly; keep UI responsive.
        self._chat_ticker = Clock.schedule_interval(self._poll_chat, 3.0)
        self._poll_chat(0)

    def _stop_chat_polling(self) -> None:
        if self._chat_ticker:
            try:
                self._chat_ticker.cancel()
            except Exception:
                pass
            self._chat_ticker = None

    def _poll_chat(self, _dt) -> None:
        sid = int(self.session_id or 0)
        if sid <= 0:
            # Not connected yet: keep overlay empty.
            Clock.schedule_once(lambda *_: self._clear_chat_overlay(), 0)
            return

        def work():
            try:
                data = api_get_messages(session_id=sid)
                msgs = data.get("messages") or []
                msgs = list(msgs)[-5:]  # show only last five

                def update_ui(*_):
                    box = self.ids.get("chat_box")
                    if not box:
                        return

                    try:
                        box.clear_widgets()
                    except Exception:
                        return

                    from kivy.uix.label import Label

                    me = get_user() or {}
                    try:
                        my_id = int(me.get("id") or 0)
                    except Exception:
                        my_id = 0

                    for m in msgs:
                        text = str(m.get("message") or "")
                        sender_name = str(m.get("sender_name") or m.get("sender") or "")
                        try:
                            sender_id = int(m.get("sender_id") or 0)
                        except Exception:
                            sender_id = 0
                        who = "Me" if (my_id and sender_id == my_id) else (sender_name or "Partner")
                        msg_text = f"[b]{who}[/b]: {text}"

                        lbl = Label(
                            text=msg_text,
                            markup=True,
                            size_hint_y=None,
                            height=dp(22),
                            size_hint_x=1,
                            halign="left",
                            valign="middle",
                            text_size=(box.width, None),
                            color=(1, 1, 1, 1),
                        )
                        # Keep wrapping width in sync with layout allocation.
                        lbl.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
                        # Only adjust height based on texture; never set width from texture_size.
                        lbl.bind(texture_size=lambda inst, s: setattr(inst, "height", s[1] + dp(6)))

                        box.add_widget(lbl)

                    # Scroll to bottom (latest) in the small overlay.
                    scroll = self.ids.get("chat_overlay")
                    if scroll:
                        try:
                            scroll.scroll_y = 0
                        except Exception:
                            pass

                Clock.schedule_once(update_ui, 0)
            except Exception:
                # Suppress polling errors.
                pass

        Thread(target=work, daemon=True).start()

    def send_message(self) -> None:
        sid = int(self.session_id or 0)
        if sid <= 0:
            return
        inp = self.ids.get("chat_input")
        if not inp:
            return
        msg = (inp.text or "").strip()
        if not msg:
            return

        inp.text = ""

        def work():
            try:
                api_post_message(session_id=sid, message=msg)
                Clock.schedule_once(lambda *_: self._poll_chat(0), 0)
            except ApiError:
                pass

        Thread(target=work, daemon=True).start()

    def go_back(self) -> None:
        self._stop_spinner()
        self._cancel_retry()
        self._stop_camera()
        self._stop_chat_polling()
        self._reset_session_chat()
        if self.manager:
            self.manager.current = "choose"

    def _clear_chat_overlay(self) -> None:
        """
        Clear visible chat messages overlay (ephemeral UI).
        Backend history remains accessible from "Chat History".
        """
        box = self.ids.get("chat_box")
        if box:
            try:
                box.clear_widgets()
            except Exception:
                pass
        scroll = self.ids.get("chat_overlay")
        if scroll:
            try:
                scroll.scroll_y = 0
            except Exception:
                pass

    def _reset_session_chat(self) -> None:
        self.session_id = 0
        self.match_user_id = 0
        self._clear_chat_overlay()

    def _set_session_chat(self, *, session_id: int, match_user_id: int) -> None:
        """
        Set the active 1:1 chat context.
        If a new match arrives, clear the overlay (UI only).
        """
        try:
            session_id = int(session_id or 0)
        except Exception:
            session_id = 0
        try:
            match_user_id = int(match_user_id or 0)
        except Exception:
            match_user_id = 0

        changed = (int(self.session_id or 0) != session_id) or (int(self.match_user_id or 0) != match_user_id)
        self.session_id = session_id
        self.match_user_id = match_user_id
        if changed:
            self._clear_chat_overlay()
