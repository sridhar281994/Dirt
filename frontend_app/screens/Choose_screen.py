from __future__ import annotations

from threading import Thread
from typing import Any, Dict, Optional

from kivy.clock import Clock
from kivy.properties import NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.app import App

from frontend_app.utils.api import (
    ApiError,
    api_next_profile,
    api_start_session,
    api_swipe,
    api_verify_subscription,
)
from frontend_app.utils.storage import clear, get_user, set_user


SUBSCRIPTION_PLANS = {
    "text_hour": 50,
    "text_10min": 10,
    "voice_hour": 200,
    "voice_10min": 30,
}


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        p = Popup(
            title=title,
            content=Label(text=str(msg)),
            size_hint=(0.75, 0.35),
            auto_dismiss=True,
        )
        p.open()
        Clock.schedule_once(lambda _dt: p.dismiss(), 2.3)

    Clock.schedule_once(_open, 0)


class ChooseScreen(Screen):
    preference = StringProperty("both")  # male|female|both
    current_profile_id = NumericProperty(0)
    current_name = StringProperty("")
    current_username = StringProperty("")
    current_country = StringProperty("")
    current_desc = StringProperty("")
    current_image_url = StringProperty("")
    
    # Logged-in user info
    my_name = StringProperty("")
    my_country = StringProperty("")
    
    # Touch handling
    _touch_start_x = None

    def on_pre_enter(self, *args):
        # Update logged-in user info
        u = get_user() or {}
        self.my_name = str(u.get("name") or "User")
        self.my_country = str(u.get("country") or "")
        
        self.refresh_profile()
        # Show hint popup on enter
        Clock.schedule_once(lambda dt: self._show_swipe_hint(), 0.5)

    def _show_swipe_hint(self):
        _popup("Hint", "Swipe Left/Right to browse profiles.")

    def on_settings_select(self, text):
        if text == "Subscribe":
            self.subscribe("text_hour") # Default or show options? 
            # User interface has separate buttons for plans. 
            # Maybe show a popup with plans?
            _popup("Info", "Select a plan from the bottom menu.")
        elif text == "Change Password":
            if self.manager:
                self.manager.current = "forgot_password"
        elif text == "History":
            if self.manager:
                self.manager.current = "user_match"
        elif text == "Logout":
            self.logout()
        
        # Reset spinner text
        spinner = self.ids.get("settings_spinner")
        if spinner:
            spinner.text = "⚙️"

    def set_preference(self, value: str) -> None:
        v = (value or "").strip().lower()
        if v not in {"male", "female", "both"}:
            return

        u = get_user() or {}
        my_gender = str(u.get("gender") or "").strip().lower()
        subscribed = bool(u.get("is_subscribed"))

        # Opposite gender selection requires subscription
        if v != "both" and my_gender in {"male", "female"} and v != my_gender and not subscribed:
            _popup("Subscription", "Opposite gender preference requires subscription.")
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
            self.current_name = ""
            self.current_username = ""
            self.current_country = ""
            self.current_desc = ""
            self.current_image_url = ""
            return
        self.current_profile_id = int(prof.get("id") or 0)
        self.current_name = str(prof.get("name") or "")
        self.current_username = str(prof.get("username") or "")
        self.current_country = str(prof.get("country") or "")
        self.current_desc = str(prof.get("description") or "")
        self.current_image_url = self._normalize_image_url(str(prof.get("image_url") or ""))

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """
        Allow backend to return:
        - absolute URLs (https://...)
        - absolute paths (/static/...)
        - relative paths (static/...)
        """
        u = (url or "").strip()
        if not u:
            return ""
        if "://" in u:
            return u
        # Use same env default used by API client
        import os
        base = os.getenv("BACKEND_URL", "https://dirt-0atr.onrender.com")
        base = (base or "").rstrip("/")
        if not u.startswith("/"):
            u = "/" + u
        return f"{base}{u}"

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
    
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self._touch_start_x = touch.x
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        if self._touch_start_x is not None:
            if touch.x - self._touch_start_x > 100:  # Swipe Right
                self.swipe_right()
            elif self._touch_start_x - touch.x > 100:  # Swipe Left
                self.swipe_left()
            self._touch_start_x = None
        return super().on_touch_up(touch)

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
    
    def start_public_chat(self) -> None:
        # Navigate to public chat screen
        if self.manager.has_screen("public_chat"):
            self.manager.current = "public_chat"
        else:
            _popup("Error", "Public chat screen not found.")

    def subscribe(self, plan_key: str) -> None:
        """
        Initiates Google Play subscription.
        plan_key must be one of SUBSCRIPTION_PLANS keys:
        'text_hour', 'text_10min', 'voice_hour', 'voice_10min'
        """
        if plan_key not in SUBSCRIPTION_PLANS:
            _popup("Error", "Invalid subscription plan.")
            return

        def purchase_flow():
            try:
                # Mock purchase token for now as we don't have Google Play integration
                purchase_token = f"mock_token_{plan_key}"

                # Verify subscription with backend
                valid = api_verify_subscription(purchase_token=purchase_token, plan_key=plan_key)
                if not valid:
                    raise ApiError("Subscription verification failed.")

                # Update user subscription status
                u = get_user() or {}
                u["is_subscribed"] = True
                set_user(u)
                _popup("Success", f"Subscription activated: {plan_key}")

            except Exception as exc:
                _popup("Subscription Error", str(exc))

        Thread(target=purchase_flow, daemon=True).start()

    def logout(self) -> None:
        clear()
        if self.manager:
            self.manager.current = "login"
