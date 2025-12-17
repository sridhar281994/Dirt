from __future__ import annotations
import os
import sys

if __name__ == "__main__":
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ""))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

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
        Builder.load_file("kv/screens.kv")

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
            from android.permissions import request_permissions, Permission
            request_permissions([
                Permission.CAMERA,
                Permission.RECORD_AUDIO,
                Permission.INTERNET
            ])

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
