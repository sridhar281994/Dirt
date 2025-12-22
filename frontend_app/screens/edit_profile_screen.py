from threading import Thread

from kivy.clock import Clock
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen
from kivy.uix.popup import Popup
from kivy.uix.label import Label
from kivy.utils import platform

from frontend_app.utils.api import ApiError, api_update_profile, api_verify_subscription
from frontend_app.utils.storage import get_user, set_session, get_token, get_remember_me, clear, set_user
from frontend_app.utils.billing import BillingManager


SUBSCRIPTION_PLANS = {
    "text_hour": 50,
    "text_10min": 10,
    "video_hour": 200,
    "video_10min": 30,
}

SKU_MAPPING = {
    "text_hour": "text_hour", 
    "text_10min": "text_10min",
    "video_hour": "video_hour", 
    "video_10min": "video_10min",
}


class EditProfileScreen(Screen):
    current_name = StringProperty("")
    current_image_url = StringProperty("")
    
    billing_manager = None

    def on_pre_enter(self, *args):
        user = get_user() or {}
        self.current_name = str(user.get("name") or "")
        self.current_image_url = str(user.get("image_url") or "")
        
        # Initialize billing if needed
        if not self.billing_manager:
            self.billing_manager = BillingManager(self._on_billing_success)
            if platform == "android":
                self.billing_manager.query_sku_details(list(SKU_MAPPING.values()))

    def go_back(self):
        if self.manager:
            self.manager.current = "choose"

    def save_profile(self):
        name = self.ids.name_input.text.strip()
        image_url = self.ids.image_url_input.text.strip()

        if not name:
            self._popup("Error", "Name cannot be empty.")
            return

        def work():
            try:
                data = api_update_profile(name=name, image_url=image_url)
                # Update local session with new user data
                user = data.get("user") or {}
                token = get_token()
                remember = get_remember_me()
                set_session(token=token, user=user, remember=remember)

                def success(*_):
                    self._popup("Success", "Profile updated.")
                    self.go_back()
                Clock.schedule_once(success, 0)
            except ApiError as exc:
                self._popup("Error", str(exc))
            except Exception as e:
                self._popup("Error", f"Failed to update: {str(e)}")

        Thread(target=work, daemon=True).start()
    
    def subscribe(self, plan_key: str) -> None:
        if plan_key not in SUBSCRIPTION_PLANS:
            self._popup("Error", "Invalid subscription plan.")
            return

        if platform == "android":
            if not self.billing_manager or not self.billing_manager.connected:
                if self.billing_manager:
                     self.billing_manager.start_connection()
                self._popup("Error", "Billing service connecting... Please try again.")
                return
            
            sku = SKU_MAPPING.get(plan_key)
            if not sku:
                self._popup("Error", "Product configuration error.")
                return

            self.billing_manager.purchase(sku)
        else:
            self._popup("Info", "Google Play Billing is only available on Android.")

    def _on_billing_success(self, sku, purchase_token, order_id):
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
                    raise ApiError("Server verification failed.")

                u = get_user() or {}
                u["is_subscribed"] = True
                set_user(u)
                
                Clock.schedule_once(lambda dt: self._popup("Success", f"Subscription activated!"), 0)
            except Exception as exc:
                self._popup("Subscription Error", str(exc))

        Thread(target=verify_server, daemon=True).start()

    def go_history(self):
        if self.manager:
            self.manager.current = "user_match"

    def change_password(self):
        if self.manager:
            fp = self.manager.get_screen("forgot_password")
            if hasattr(fp, "open_from"):
                fp.open_from(source_screen="edit_profile", title="Change Password")
            self.manager.current = "forgot_password"

    def logout(self):
        clear()
        if self.manager:
            self.manager.current = "login"

    def _popup(self, title, msg):
        def _open(*_):
            popup = Popup(
                title=title,
                content=Label(text=str(msg)),
                size_hint=(0.7, 0.3),
                auto_dismiss=True,
            )
            popup.open()
            Clock.schedule_once(lambda dt: popup.dismiss(), 2)
        Clock.schedule_once(_open, 0)
