from __future__ import annotations
import os
import sys

#
# IMPORTANT (Android packaging):
# Buildozer runs this file as the entrypoint (e.g. `frontend_app/main.py`).
# When a script inside a package folder is executed, Python adds THAT folder
# to `sys.path`, not the repository root. Our imports use `frontend_app.*`,
# so we must ensure the repo root (parent of `frontend_app/`) is on `sys.path`.
#
APP_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(APP_DIR, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.utils import platform

from frontend_app.screens.Choose_screen import ChooseScreen
from frontend_app.screens.chat_screen import ChatScreen
from frontend_app.screens.login_screen import LoginScreen
from frontend_app.screens.public_chat_screen import PublicChatScreen
from frontend_app.screens.register_screen import RegisterScreen
from frontend_app.screens.video_screen import VideoScreen
from frontend_app.screens.reset_password_screen import ForgotPasswordScreen, ResetPasswordScreen
from frontend_app.screens.usermatch_screen import UserMatchScreen
from frontend_app.utils.storage import get_user

class WelcomeScreen(Screen):
    pass

class ChatApp(App):
    def build(self):
        self.title = "frends-Chat"
        app_dir = os.path.dirname(__file__)
        self.icon = os.path.join(app_dir, "assets", "icon.png")
        Builder.load_file(os.path.join(app_dir, "kv", "screens.kv"))

        self.sm = ScreenManager()
        self.sm.add_widget(WelcomeScreen(name="welcome"))
        self.sm.add_widget(LoginScreen(name="login"))
        self.sm.add_widget(RegisterScreen(name="register"))
        self.sm.add_widget(ForgotPasswordScreen(name="forgot_password"))
        self.sm.add_widget(ResetPasswordScreen(name="reset_password"))
        self.sm.add_widget(ChooseScreen(name="choose"))
        self.sm.add_widget(ChatScreen(name="chat"))
        self.sm.add_widget(PublicChatScreen(name="public_chat"))
        self.sm.add_widget(VideoScreen(name="video"))
        self.sm.add_widget(UserMatchScreen(name="user_match"))

        self.sm.current = "welcome"

        # Root layout to hold ScreenManager and overlay Timer
        root = FloatLayout()
        root.add_widget(self.sm)

        # Timer Label (Overlay)
        self.timer_label = Label(
            text="",
            size_hint=(None, None),
            size=(200, 50),
            pos_hint={'top': 1, 'right': 1},
            color=(0, 1, 0, 1),
            bold=True
        )
        root.add_widget(self.timer_label)

        Clock.schedule_interval(self.update_timer, 1.0)

        return root

    def on_start(self):
        """Request permissions on Android."""
        if platform == "android":
            try:
                from android.permissions import request_permissions, Permission

                # INTERNET is a normal permission (not runtime/dangerous) and may not exist
                # in android.permissions.Permission on some setups. Only request runtime ones.
                request_permissions([Permission.CAMERA, Permission.RECORD_AUDIO])
            except Exception as exc:
                # Make startup failures visible in logcat.
                try:
                    import traceback

                    print("Permission request failed:", exc)
                    print(traceback.format_exc())
                except Exception:
                    pass

    def update_timer(self, dt):
        user = get_user() or {}
        if user.get("is_subscribed"):
            # specific logic for timer not fully defined, showing active status
            # If we had expiry, we would calculate remaining time here.
            self.timer_label.text = "Subscription Active"
        else:
            self.timer_label.text = ""

if __name__ == "__main__":
    ChatApp().run()
