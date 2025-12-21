from __future__ import annotations

from threading import Thread
from typing import Any, Dict, Optional

from kivy.clock import Clock
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.app import App
from kivy.uix.spinner import Spinner

class CustomSpinner(Spinner):
    def __init__(self, **kwargs):
        self.dropdown_width = kwargs.pop('dropdown_width', None)
        super().__init__(**kwargs)
        # Don't touch Spinner internals during KV rule application.
        # Ensure we only resize the dropdown once it exists.
        Clock.schedule_once(self._update_dropdown_size, 0)
        self.bind(on_release=self._update_dropdown_size)

    def _update_dropdown_size(self, *largs):
        if self.dropdown_width:
            dropdown = getattr(self, "_dropdown", None)
            if dropdown:
                dropdown.width = self.dropdown_width
            return
        super()._update_dropdown_size(*largs)


from frontend_app.utils.api import (
    ApiError,
    api_next_profile,
    api_start_session,
    api_swipe,
    api_verify_subscription,
    api_video_match,
)
from frontend_app.utils.billing import BillingManager
from frontend_app.utils.storage import clear, get_user, set_user
from kivy.utils import platform


SUBSCRIPTION_PLANS = {
    "text_hour": 50,
    "text_10min": 10,
    "video_hour": 200,
    "video_10min": 30,
}

# Real SKU mapping (Replace with actual Google Play Product IDs)
SKU_MAPPING = {
    "text_hour": "text_hour", 
    "text_10min": "text_10min",
    "video_hour": "video_hour", 
    "video_10min": "video_10min",
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
    preference = StringProperty("both")  # male|female|both (internal logic uses lowercase)
    current_profile_id = NumericProperty(0)
    current_name = StringProperty("")
    current_username = StringProperty("")
    current_country = StringProperty("")
    current_desc = StringProperty("")
    current_image_url = StringProperty("")
    current_is_online = BooleanProperty(False)
    
    # Logged-in user info
    my_name = StringProperty("")
    my_country = StringProperty("")
    my_desc = StringProperty("")
    
    # Touch handling
    _touch_start_x = None
    
    billing_manager = None

    def on_pre_enter(self, *args):
        # Initialize billing if needed
        if not self.billing_manager:
            self.billing_manager = BillingManager(self._on_billing_success)
            # Pre-query SKUs
            if platform == "android":
                self.billing_manager.query_sku_details(list(SKU_MAPPING.values()))

        # Update logged-in user info
        u = get_user() or {}
        self.my_name = str(u.get("name") or "User")
        self.my_country = str(u.get("country") or "")
        self.my_desc = str(u.get("description") or "")
        
        # Enforce gender preference for non-subscribed users
        if not bool(u.get("is_subscribed")):
            self.preference = "both"
            spinner = self.ids.get("pref_spinner")
            if spinner:
                spinner.text = "Both"
                # Do not disable spinner, allow user to see options but not select them
                # spinner.disabled = True
        
        if not hasattr(self, "_next_profile_cache"):
            self._next_profile_cache = None
        
        self.refresh_profile()

    def on_settings_select(self, text):
        if text == "Subscribe":
            self.subscribe("text_hour") # Default or show options? 
            # User interface has separate buttons for plans. 
            # Maybe show a popup with plans?
            _popup("Info", "Select a plan from the bottom menu.")
        elif text == "Change Password":
            if self.manager:
                # Reuse the OTP-based flow, but make Back return here.
                fp = self.manager.get_screen("forgot_password")
                if hasattr(fp, "open_from"):
                    fp.open_from(source_screen="choose", title="Change Password")
                self.manager.current = "forgot_password"
        elif text == "History":
            if self.manager:
                self.manager.current = "user_match"
        elif text == "Logout":
            self.logout()
        
        # Reset spinner text
        spinner = self.ids.get("settings_spinner")
        if spinner:
            spinner.text = "..."

    def set_preference(self, value: str) -> None:
        v = (value or "").strip().lower()
        if v not in {"male", "female", "both"}:
            return

        # Check subscription if user tries to change preference
        u = get_user() or {}
        if not bool(u.get("is_subscribed")) and v != "both":
            _popup("Premium", "Gender selection is for paid subscribers only.")
            # Revert UI
            spinner = self.ids.get("pref_spinner")
            if spinner:
                spinner.text = "Both"
            return

        self.preference = v
        self._next_profile_cache = None
        self.refresh_profile()

    def refresh_profile(self) -> None:
        # Check cache first
        if getattr(self, "_next_profile_cache", None):
            prof = self._next_profile_cache
            self._next_profile_cache = None
            self._set_profile(prof)
            self._prefetch_next_profile()
            return

        def work():
            try:
                data = api_next_profile(preference=self.preference)
                prof = (data or {}).get("profile")
                if not prof:
                    Clock.schedule_once(lambda *_: self._set_profile(None), 0)
                    return
                Clock.schedule_once(lambda *_: self._set_profile(prof), 0)
                # Prefetch next
                Clock.schedule_once(lambda *_: self._prefetch_next_profile(), 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def _prefetch_next_profile(self) -> None:
        def work():
            try:
                data = api_next_profile(preference=self.preference)
                prof = (data or {}).get("profile")
                if prof:
                    self._next_profile_cache = prof
            except Exception:
                pass
        
        Thread(target=work, daemon=True).start()

    def _set_profile(self, prof: Optional[Dict[str, Any]]) -> None:
        if not prof:
            self.current_profile_id = 0
            self.current_name = ""
            self.current_username = ""
            self.current_country = ""
            self.current_desc = ""
            self.current_image_url = ""
            self.current_is_online = False
            return
        self.current_profile_id = int(prof.get("id") or 0)
        self.current_name = str(prof.get("name") or "")
        self.current_username = str(prof.get("username") or "")
        self.current_country = str(prof.get("country") or "")
        self.current_desc = str(prof.get("description") or "")
        self.current_is_online = bool(prof.get("is_online") or False)
        raw_img = str(prof.get("image_url") or "")
        if raw_img.strip():
            self.current_image_url = self._normalize_image_url(raw_img)
        else:
            # Never show "No photo" to users; use a lightweight placeholder.
            self.current_image_url = self._fallback_avatar_url(self.current_name or self.current_username or "User")

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

    @staticmethod
    def _fallback_avatar_url(name: str) -> str:
        # External placeholder image that renders initials.
        import urllib.parse
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

    def swipe_left(self) -> None:
        self._swipe("left")

    def swipe_right(self) -> None:
        self._swipe("right")

    def _swipe(self, direction: str) -> None:
        tid = int(self.current_profile_id or 0)
        if tid <= 0:
            return

        # Optimistic UI: Load next profile immediately
        self.refresh_profile()

        def work():
            try:
                api_swipe(target_user_id=tid, direction=direction)
            except ApiError as exc:
                # Swipe failed, but we already moved on. 
                # Ideally we might queue this or retry, but for now just log/ignore for UI speed.
                pass

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
        # Chat is subscription-only.
        u = get_user() or {}
        if not bool(u.get("is_subscribed")):
            _popup("Subscription", "Chat requires an active subscription.")
            return

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
                        chat.set_session(session_id=sid, mode=mode, target_user_id=tid)
                        self.manager.current = "chat"

                Clock.schedule_once(go, 0)
            except ApiError as exc:
                _popup("Subscription", str(exc))

        Thread(target=work, daemon=True).start()

    def start_video_chat(self) -> None:
        """Start a video chat from the current profile card."""
        self.start_chat("video")

    def start_random_video_chat(self) -> None:
        if not self.manager:
            return
        
        # Preference is already set and locked for free users in on_pre_enter/set_preference
        video = self.manager.get_screen("video")
        video.start_random(preference=self.preference)
        self.manager.current = "video"
    
    def start_public_chat(self) -> None:
        if self.manager.has_screen("public_chat"):
            self.manager.current = "public_chat"
        else:
            _popup("Error", "Public chat screen not found.")

    def subscribe(self, plan_key: str) -> None:
        if plan_key not in SUBSCRIPTION_PLANS:
            _popup("Error", "Invalid subscription plan.")
            return

        if platform == "android":
            if not self.billing_manager or not self.billing_manager.connected:
                # Try to reconnect or warn
                if self.billing_manager:
                     self.billing_manager.start_connection()
                _popup("Error", "Billing service connecting... Please try again.")
                return
            
            sku = SKU_MAPPING.get(plan_key)
            if not sku:
                _popup("Error", "Product configuration error.")
                return

            self.billing_manager.purchase(sku)
        else:
            _popup("Info", "Google Play Billing is only available on Android.")

    def _on_billing_success(self, sku, purchase_token, order_id):
        # Identify plan from SKU
        plan_key = None
        for k, v in SKU_MAPPING.items():
            if v == sku:
                plan_key = k
                break
        
        if not plan_key:
            plan_key = "unknown"

        def verify_server():
            try:
                valid = api_verify_subscription(purchase_token=purchase_token, plan_key=plan_key)
                if not valid:
                    # In production, you might want to retry or not consume, but here we just warn
                    raise ApiError("Server verification failed.")

                u = get_user() or {}
                u["is_subscribed"] = True
                set_user(u)
                
                Clock.schedule_once(lambda dt: self._unlock_ui(plan_key), 0)
            except Exception as exc:
                _popup("Subscription Error", str(exc))

        Thread(target=verify_server, daemon=True).start()

    def _unlock_ui(self, plan_key):
        spinner = self.ids.get("pref_spinner")
        if spinner:
            spinner.disabled = False
        _popup("Success", f"Subscription activated!")

    def logout(self) -> None:
        clear()
        if self.manager:
            self.manager.current = "login"
