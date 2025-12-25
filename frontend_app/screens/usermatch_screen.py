from __future__ import annotations
from threading import Thread
from kivy.clock import Clock
from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.image import AsyncImage
from kivy.metrics import dp, sp

from frontend_app.utils.api import api_get_history, ApiError
from frontend_app.utils.storage import get_last_read_message_id, get_user

class UserMatchScreen(Screen):
    def on_pre_enter(self, *args):
        self.refresh_history()

    def refresh_history(self):
        def work():
            try:
                data = api_get_history()
                history = data.get("history") or []
                Clock.schedule_once(lambda *_: self._display_history(history), 0)
            except ApiError as exc:
                print(f"Messages error: {exc}")

        Thread(target=work, daemon=True).start()

    def _display_history(self, history):
        box = self.ids.get("history_box")
        if not box:
            return
        box.clear_widgets()

        if not history:
            lbl = Label(text="No messages yet.", size_hint_y=None, height=dp(40), color=(0.8, 0.8, 0.8, 1))
            box.add_widget(lbl)
            return

        me = get_user() or {}
        try:
            my_id = int(me.get("id") or 0)
        except Exception:
            my_id = 0

        for item in history:
            # item = {user_id, name, image_url, last_seen, session_id, mode, is_on_call, is_online}
            row = BoxLayout(size_hint_y=None, height=dp(80), spacing=dp(10), padding=dp(5))
            
            # Avatar
            img_url = item.get("image_url") or ""
            if img_url:
                img = AsyncImage(source=img_url, size_hint_x=None, width=dp(70), allow_stretch=True)
                row.add_widget(img)
            else:
                lbl_ph = Label(text="?", size_hint_x=None, width=dp(70))
                row.add_widget(lbl_ph)

            # Message read/unread summary (local-only).
            sess_id = int(item.get("session_id") or 0)
            last_mid = int(item.get("last_message_id") or 0)
            last_sender = int(item.get("last_message_sender_id") or 0)
            last_text = str(item.get("last_message_text") or "")
            last_read = get_last_read_message_id(session_id=sess_id)
            has_messages = last_mid > 0
            is_unread = bool(has_messages and last_mid > last_read and last_sender and (my_id and last_sender != my_id))

            # Info
            info = BoxLayout(orientation="vertical")
            name_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(24))
            name_lbl = Label(
                text=str(item.get("name") or "User"),
                halign="left",
                valign="middle",
                bold=bool(is_unread),
                color=(1, 1, 1, 1),
            )
            name_lbl.bind(size=name_lbl.setter("text_size"))
            name_row.add_widget(name_lbl)

            dot = Label(
                text="●" if has_messages else "",
                size_hint_x=None,
                width=dp(18),
                halign="right",
                valign="middle",
                color=(1, 0.2, 0.2, 1) if is_unread else (0.6, 0.6, 0.6, 1),
                font_size=sp(14),
            )
            dot.bind(size=dot.setter("text_size"))
            name_row.add_widget(dot)
            info.add_widget(name_row)
            
            # Status Notification
            status_text = ""
            status_color = (0.5, 0.5, 0.5, 1)
            
            if item.get("is_on_call"):
                status_text = "Busy (On Call)"
                status_color = (1, 0.3, 0.3, 1)
            elif not item.get("is_online"):
                status_text = "Offline"
                status_color = (0.6, 0.6, 0.6, 1)
            else:
                status_text = "Online"
                status_color = (0.2, 0.9, 0.2, 1)
                
            status_lbl = Label(text=status_text, font_size=sp(12), color=status_color, halign="left", valign="middle")
            status_lbl.bind(size=status_lbl.setter('text_size'))
            info.add_widget(status_lbl)

            # Last message preview (highlight unread).
            preview = (last_text or "").strip()
            if len(preview) > 40:
                preview = preview[:40].rstrip() + "…"
            if has_messages:
                prev_lbl = Label(
                    text=("[b]NEW:[/b] " if is_unread else "") + preview,
                    markup=True,
                    font_size=sp(11),
                    color=(1, 1, 1, 1) if is_unread else (0.85, 0.85, 0.85, 1),
                    halign="left",
                    valign="middle",
                )
                prev_lbl.bind(size=prev_lbl.setter("text_size"))
                info.add_widget(prev_lbl)

            last = str(item.get("last_seen") or "")
            if last:
                last = last.replace("T", " ")[:16]
            time_lbl = Label(text=last, font_size=sp(10), color=(0.7,0.7,0.7,1))
            info.add_widget(time_lbl)
            
            row.add_widget(info)

            # Chat Button
            mode = str(item.get("mode") or "text")
            
            btn = Button(text="Chat", size_hint_x=None, width=dp(80), background_color=(0.3, 0.6, 0.9, 1))
            btn.bind(on_release=lambda x, sid=sess_id, m=mode: self.open_chat(sid, m))
            row.add_widget(btn)

            box.add_widget(row)

    def open_chat(self, session_id, mode):
        if not session_id:
            return

        # Chat is subscription-only.
        u = get_user() or {}
        if mode in {"text", "voice"} and not bool(u.get("is_subscribed")):
            return
        
        chat = self.manager.get_screen("chat")
        chat.set_session(session_id=session_id, mode=mode)
        self.manager.current = "chat"

    def go_back(self):
        self.manager.current = "choose"
