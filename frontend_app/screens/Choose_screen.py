from __future__ import annotations

from threading import Thread
from typing import Any, Dict, Optional

from kivy.clock import Clock
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_demo_subscribe, api_next_profile, api_start_session, api_swipe
from frontend_app.utils.storage import clear, get_user, set_user


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        p = Popup(title=title, content=Label(text=str(msg)), size_hint=(0.75, 0.35), auto_dismiss=True)
        p.open()
        Clock.schedule_once(lambda _dt: p.dismiss(), 2.3)

    Clock.schedule_once(_open, 0)


class ChooseScreen(Screen):
    preference = StringProperty("both")  # male|female|both
    current_profile_id = NumericProperty(0)
    current_name = StringProperty("")
    current_country = StringProperty("")
    current_desc = StringProperty("")
    current_image_url = StringProperty("")

    def on_pre_enter(self, *args):
        self.refresh_profile()

    def set_preference(self, value: str) -> None:
        v = (value or "").strip().lower()
        if v not in {"male", "female", "both"}:
            return

        u = get_user() or {}
        my_gender = str(u.get("gender") or "").strip().lower()
        subscribed = bool(u.get("is_subscribed"))

        # Requirement: selecting opposite gender requires subscription.
        if v != "both" and my_gender in {"male", "female"} and v != my_gender and not subscribed:
            _popup("Subscription", "Opposite gender preference requires subscription.")
            # Revert spinner back to both (if present)
            spinner = self.ids.get("pref_spinner") if hasattr(self, "ids") else None
            if spinner is not None:
                spinner.text = "both"
            v = "both"

        self.preference = v
        self.refresh_profile()

    def refresh_profile(self) -> None:
        def work():
            try:
                data = api_next_profile(preference=self.preference)
                prof = (data or {}).get("profile")
                if not prof:
                    Clock.schedule_once(lambda *_: self._set_profile(None), 0)
                    return
                Clock.schedule_once(lambda *_: self._set_profile(prof), 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def _set_profile(self, prof: Optional[Dict[str, Any]]) -> None:
        if not prof:
            self.current_profile_id = 0
            self.current_name = "No more profiles"
            self.current_country = ""
            self.current_desc = ""
            self.current_image_url = ""
            return
        self.current_profile_id = int(prof.get("id") or 0)
        self.current_name = str(prof.get("name") or "")
        self.current_country = str(prof.get("country") or "")
        self.current_desc = str(prof.get("description") or "")
        self.current_image_url = str(prof.get("image_url") or "")

    def swipe_left(self) -> None:
        self._swipe("left")

    def swipe_right(self) -> None:
        self._swipe("right")

    def _swipe(self, direction: str) -> None:
        tid = int(self.current_profile_id or 0)
        if tid <= 0:
            return

        def work():
            try:
                api_swipe(target_user_id=tid, direction=direction)
                Clock.schedule_once(lambda *_: self.refresh_profile(), 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def start_chat(self, mode: str) -> None:
        tid = int(self.current_profile_id or 0)
        if tid <= 0:
            _popup("Info", "No profile selected.")
            return

        def work():
            try:
                data = api_start_session(target_user_id=tid, mode=mode)
                sess = data.get("session") or {}
                sid = int(sess.get("id") or 0)
                if not sid:
                    raise ApiError("Failed to start session.")

                def go(*_):
                    if mode == "video":
                        vid = self.manager.get_screen("video")
                        vid.set_session(session_id=sid)
                        self.manager.current = "video"
                    else:
                        chat = self.manager.get_screen("chat")
                        chat.set_session(session_id=sid, mode=mode)
                        self.manager.current = "chat"

                Clock.schedule_once(go, 0)
            except ApiError as exc:
                _popup("Subscription", str(exc))

        Thread(target=work, daemon=True).start()

    def subscribe(self) -> None:
        # Demo subscription activation (backend marks user.is_subscribed = True).
        _popup(
            "Subscription plans",
            "Text chat: ₹50/hour or ₹10/10 minutes\n"
            "Video/Voice chat: ₹200/hour or ₹30/10 minutes\n\n"
            "This button activates a demo subscription in backend.",
        )
        def work():
            try:
                api_demo_subscribe()
                u = get_user()
                u["is_subscribed"] = True
                set_user(u)
                _popup("Success", "Subscription activated (demo).")
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def logout(self) -> None:
        clear()
        if self.manager:
            self.manager.current = "login"

