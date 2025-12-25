from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.metrics import dp

from frontend_app.utils.api import ApiError, api_get_messages, api_post_message
from frontend_app.utils.storage import get_last_read_message_id, get_user, set_last_read_message_id


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        p = Popup(title=title, content=Label(text=str(msg)), size_hint=(0.75, 0.35), auto_dismiss=True)
        p.open()
        Clock.schedule_once(lambda _dt: p.dismiss(), 2.0)

    Clock.schedule_once(_open, 0)


class ChatScreen(Screen):
    session_id = NumericProperty(0)
    mode = StringProperty("text")
    target_user_id = NumericProperty(0)

    def set_session(self, *, session_id: int, mode: str, target_user_id: int = 0):
        self.session_id = int(session_id)
        self.mode = mode
        self.target_user_id = int(target_user_id)
        self.refresh_messages()

    def report_user(self):
        if self.target_user_id > 0:
            from frontend_app.utils.report_popup import show_report_popup
            show_report_popup(reported_user_id=self.target_user_id, context="chat")
        else:
            _popup("Info", "Cannot report user (ID unknown).")

    def go_back(self):
        if self.manager:
            self.manager.current = "choose"

    def refresh_messages(self):
        sid = int(self.session_id or 0)
        if sid <= 0:
            return

        me = get_user() or {}
        try:
            my_id = int(me.get("id") or 0)
        except Exception:
            my_id = 0
        last_read = get_last_read_message_id(session_id=sid)

        def work():
            try:
                data = api_get_messages(session_id=sid)
                msgs = data.get("messages") or []

                def render(*_):
                    box = self.ids.get("messages_box")
                    if not box:
                        return
                    box.clear_widgets()
                    max_id = 0
                    for m in msgs:
                        try:
                            mid = int(m.get("id") or 0)
                        except Exception:
                            mid = 0
                        if mid > max_id:
                            max_id = mid

                        try:
                            sender_id = int(m.get("sender_id") or 0)
                        except Exception:
                            sender_id = 0
                        text = str(m.get("message") or "")
                        is_unread = bool(mid and mid > last_read and (my_id and sender_id != my_id))

                        who = "Me" if (my_id and sender_id == my_id) else "Partner"
                        prefix = f"[b]{who}[/b]: "
                        if is_unread:
                            msg_text = f"[b]{prefix}{text}[/b]"
                            color = (1, 1, 1, 1)
                        else:
                            msg_text = f"{prefix}{text}"
                            color = (0.85, 0.85, 0.85, 1)

                        lbl = Label(
                            text=msg_text,
                            markup=True,
                            size_hint_y=None,
                            height=dp(24),
                            size_hint_x=1,
                            halign="left",
                            valign="middle",
                            text_size=(box.width, None),
                            color=color,
                        )
                        lbl.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
                        lbl.bind(texture_size=lambda inst, s: setattr(inst, "height", s[1] + dp(8)))
                        box.add_widget(lbl)

                    # Scroll to bottom (latest).
                    scroll = self.ids.get("messages_scroll")
                    if scroll is not None:
                        try:
                            scroll.scroll_y = 0
                        except Exception:
                            pass

                    # Mark everything up to the latest message as read (local-only).
                    if max_id > 0:
                        set_last_read_message_id(session_id=sid, message_id=max_id)

                Clock.schedule_once(render, 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def send_message(self):
        sid = int(self.session_id or 0)
        if sid <= 0:
            return
        inp = self.ids.get("message_input")
        msg = (inp.text or "").strip() if inp else ""
        if not msg:
            return

        def work():
            try:
                api_post_message(session_id=sid, message=msg)

                def after(*_):
                    if inp:
                        inp.text = ""
                    self.refresh_messages()

                Clock.schedule_once(after, 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

