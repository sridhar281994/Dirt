from threading import Thread

from kivy.clock import Clock
from kivy.properties import StringProperty
from kivy.uix.screenmanager import Screen
from kivy.uix.popup import Popup
from kivy.uix.label import Label

from frontend_app.utils.api import ApiError, api_update_profile
from frontend_app.utils.storage import get_user, set_session, get_token, get_remember_me

class EditProfileScreen(Screen):
    current_name = StringProperty("")
    current_image_url = StringProperty("")

    def on_pre_enter(self, *args):
        user = get_user() or {}
        self.current_name = str(user.get("name") or "")
        self.current_image_url = str(user.get("image_url") or "")

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
