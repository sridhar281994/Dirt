from __future__ import annotations

from threading import Thread

from kivy.clock import Clock
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_get_messages, api_post_message


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        p = Popup(title=title, content=Label(text=str(msg)), size_hint=(0.75, 0.35), auto_dismiss=True)
        p.open()
        Clock.schedule_once(lambda _dt: p.dismiss(), 2.0)

    Clock.schedule_once(_open, 0)


class ChatScreen(Screen):
    session_id = NumericProperty(0)
    mode = StringProperty("text")

    def set_session(self, *, session_id: int, mode: str):
        self.session_id = int(session_id)
        self.mode = mode
        self.refresh_messages()

    def go_back(self):
        if self.manager:
            self.manager.current = "choose"

    def refresh_messages(self):
        sid = int(self.session_id or 0)
        if sid <= 0:
            return

        def work():
            try:
                data = api_get_messages(session_id=sid)
                msgs = data.get("messages") or []

                def render(*_):
                    box = self.ids.get("messages_box")
                    if not box:
                        return
                    box.clear_widgets()
                    for m in msgs:
                        box.add_widget(Label(text=f"{m.get('sender_id')}: {m.get('message')}", size_hint_y=None, height=24))

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

