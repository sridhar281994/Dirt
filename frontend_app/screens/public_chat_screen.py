from __future__ import annotations

from threading import Thread
from typing import Any, Dict

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import ObjectProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_get_public_messages, api_post_public_message


class PublicChatScreen(Screen):
    # Rendering hundreds of Labels in one frame can trigger:
    #   [CRITICAL] [Clock] Warning, too much iteration done before the next frame...
    # Keep UI snappy by limiting + chunk-rendering across frames.
    DISPLAY_LIMIT = 200
    RENDER_CHUNK = 40

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
                Logger.info("API: about to call server")
                data = api_get_public_messages(limit=int(self.DISPLAY_LIMIT))
                msgs = list(data.get("messages") or [])
                Clock.schedule_once(lambda *_: self._display_messages(msgs, scroll_to_bottom), 0)
            except ApiError:
                Logger.exception("NETWORK ERROR")  # keep polling but don't swallow

        Thread(target=work, daemon=False).start()

    def _display_messages(self, messages, scroll_to_bottom: bool) -> None:
        box = self.ids.get("messages_box")
        if not box:
            return
        box.clear_widgets()

        # Render incrementally (one chunk per frame) to avoid Clock iteration warnings.
        msgs = list(messages)[-int(self.DISPLAY_LIMIT) :] if messages else []
        idx = 0

        def _render_chunk(_dt):
            nonlocal idx
            end = min(idx + int(self.RENDER_CHUNK), len(msgs))
            for m in msgs[idx:end]:
                sender = m.get("sender_name") or "Unknown"
                text = m.get("message") or ""
                lbl = Label(
                    text=f"[b]{sender}[/b]: {text}",
                    markup=True,
                    size_hint_y=None,
                    height=40,
                    text_size=(box.width, None),
                    halign="left",
                    valign="middle",
                    color=(1, 1, 1, 1),
                )
                box.add_widget(lbl)
            idx = end

            if idx < len(msgs):
                Clock.schedule_once(_render_chunk, 0)
                return

            if scroll_to_bottom:
                scroll = self.ids.get("messages_scroll")
                if scroll:
                    try:
                        scroll.scroll_y = 0
                    except Exception:
                        pass

        Clock.schedule_once(_render_chunk, 0)

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
                Logger.info("API: about to call server")
                api_post_public_message(message=text)
                Clock.schedule_once(lambda *_: self.refresh_messages(scroll_to_bottom=True), 0)
            except ApiError:
                Logger.exception("NETWORK ERROR")

        Thread(target=work, daemon=False).start()

    def report_chat(self):
        from frontend_app.utils.report_popup import show_report_popup
        # No specific user ID for general report, user can specify in details
        show_report_popup(reported_user_id=None, context="public_chat")

    def go_back(self) -> None:
        if self.manager:
            self.manager.current = "choose"
