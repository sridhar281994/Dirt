from __future__ import annotations

from threading import Thread
from typing import Any, Dict

from kivy.clock import Clock
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_get_public_messages, api_post_public_message


class PublicChatScreen(Screen):
    def on_pre_enter(self, *args):
        self.refresh_messages(scroll_to_bottom=True)
        # Start auto-refresh polling
        self._refresh_event = Clock.schedule_interval(self._refresh_loop, 5.0)

    def on_leave(self, *args):
        if hasattr(self, "_refresh_event"):
            self._refresh_event.cancel()

    def _refresh_loop(self, dt):
        self.refresh_messages(scroll_to_bottom=False)

    def refresh_messages(self, scroll_to_bottom: bool = False) -> None:
        def work():
            try:
                data = api_get_public_messages(limit=500)
                msgs = data.get("messages") or []
                Clock.schedule_once(lambda *_: self._display_messages(msgs, scroll_to_bottom), 0)
            except ApiError:
                pass  # suppress errors in loop

        Thread(target=work, daemon=True).start()

    def _display_messages(self, messages, scroll_to_bottom: bool) -> None:
        box = self.ids.get("messages_box")
        if not box:
            return
        box.clear_widgets()
        for m in messages:
            # Simple message display
            sender = m.get("sender_name") or "Unknown"
            text = m.get("message") or ""
            # Layout for message
            lbl = Label(
                text=f"[b]{sender}[/b]: {text}",
                markup=True,
                size_hint_y=None,
                height=40,
                text_size=(box.width, None),
                halign="left",
                valign="middle",
                color=(1, 1, 1, 1)
            )
            # Dynamic height?
            # For now fixed height is fine for simple text
            box.add_widget(lbl)
            
        if scroll_to_bottom:
            scroll = self.ids.get("messages_scroll")
            if scroll:
                scroll.scroll_y = 0

    def send_message(self) -> None:
        inp = self.ids.get("message_input")
        if not inp:
            return
        text = inp.text.strip()
        if not text:
            return
        
        # Optimistic clear
        inp.text = ""

        def work():
            try:
                api_post_public_message(message=text)
                Clock.schedule_once(lambda *_: self.refresh_messages(scroll_to_bottom=True), 0)
            except ApiError as exc:
                print(f"Send error: {exc}")

        Thread(target=work, daemon=True).start()

    def report_chat(self):
        from frontend_app.utils.report_popup import show_report_popup
        # No specific user ID for general report, user can specify in details
        show_report_popup(reported_user_id=None, context="public_chat")

    def go_back(self) -> None:
        if self.manager:
            self.manager.current = "choose"
