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


from frontend_app.screens.Choose_screen import ChooseScreen
from frontend_app.screens.chat_screen import ChatScreen
from frontend_app.screens.login_screen import LoginScreen
from frontend_app.screens.register_screen import RegisterScreen
from frontend_app.screens.video_screen import VideoScreen

class WelcomeScreen(Screen):
    pass

class ChatApp(App):
    def build(self):
        Builder.load_file("kv/screens.kv")

        sm = ScreenManager()
        sm.add_widget(WelcomeScreen(name="welcome"))
        sm.add_widget(LoginScreen(name="login"))
        sm.add_widget(RegisterScreen(name="register"))
        sm.add_widget(ChooseScreen(name="choose"))
        sm.add_widget(ChatScreen(name="chat"))
        sm.add_widget(VideoScreen(name="video"))

        sm.current = "welcome"

        self.sm = sm
        return sm



if __name__ == "__main__":
    ChatApp().run()

