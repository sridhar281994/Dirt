from __future__ import annotations
import os
import sys
import ssl

# Fix for SSL certificate verify failed error
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

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
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from kivy.logger import Logger
from kivy.utils import platform

from frontend_app.utils.storage import get_user, should_auto_login
import traceback

class WelcomeScreen(Screen):
    pass

class ChatApp(App):
    def build(self):
        self.title = "Buddymeet"
        app_dir = os.path.dirname(__file__)
        self.icon = os.path.join(app_dir, "assets", "icon.png")
        try:
            # Load KV first (this can crash if any widget/class is unknown).
            Builder.load_file(os.path.join(app_dir, "kv", "screens.kv"))

            # Import screens lazily so we can show a readable error screen
            # instead of hard-closing on startup.
            from frontend_app.screens.Choose_screen import ChooseScreen
            from frontend_app.screens.chat_screen import ChatScreen
            from frontend_app.screens.login_screen import LoginScreen
            from frontend_app.screens.public_chat_screen import PublicChatScreen
            from frontend_app.screens.register_screen import RegisterScreen
            from frontend_app.screens.video_screen import VideoScreen
            from frontend_app.screens.reset_password_screen import ForgotPasswordScreen, ResetPasswordScreen
            from frontend_app.screens.usermatch_screen import UserMatchScreen
            from frontend_app.screens.edit_profile_screen import EditProfileScreen

            self.sm = ScreenManager()
            self.sm.add_widget(WelcomeScreen(name="welcome"))
            self.sm.add_widget(LoginScreen(name="login"))
            self.sm.add_widget(RegisterScreen(name="register"))
            self.sm.add_widget(ForgotPasswordScreen(name="forgot_password"))
            self.sm.add_widget(ResetPasswordScreen(name="reset_password"))
            self.sm.add_widget(EditProfileScreen(name="edit_profile"))
            self.sm.add_widget(ChooseScreen(name="choose"))
            self.sm.add_widget(ChatScreen(name="chat"))
            self.sm.add_widget(PublicChatScreen(name="public_chat"))
            self.sm.add_widget(VideoScreen(name="video"))
            self.sm.add_widget(UserMatchScreen(name="user_match"))

            # If user opted into "Keep me logged in", skip to Choose.
            self.sm.current = "choose" if should_auto_login() else "welcome"

            # Root layout to hold ScreenManager and overlay Timer
            root = FloatLayout()
            root.add_widget(self.sm)

            # Timer Label (Overlay)
            self.timer_label = Label(
                text="",
                size_hint=(None, None),
                size=(200, 50),
                pos_hint={"top": 1, "right": 1},
                color=(0, 1, 0, 1),
                bold=True,
            )
            root.add_widget(self.timer_label)

            Clock.schedule_interval(self.update_timer, 1.0)
            return root

        except Exception:
            # If the app crashes on Android, users only see the Python logo briefly.
            # This keeps the app alive and shows the real error + puts it in logcat.
            tb = traceback.format_exc()
            Logger.exception("App crashed during build()")
            print(tb)

            sv = ScrollView()
            lbl = Label(
                text=tb,
                size_hint_y=None,
                text_size=(self._get_window_width(), None),
                halign="left",
                valign="top",
            )
            # Make label tall enough to scroll.
            lbl.bind(texture_size=lambda _i, s: setattr(lbl, "height", s[1] + 40))
            sv.add_widget(lbl)
            return sv

    @staticmethod
    def _get_window_width() -> int:
        try:
            from kivy.core.window import Window

            return int(Window.width or 360)
        except Exception:
            return 360

    def on_start(self):
        """
        Android runtime permissions.

        Important: some python-for-android/android.permissions versions will crash if
        `request_permissions()` is called without a callback (the result delivery tries
        to call a None callback). Always provide a callback and never start camera/mic
        until permission is confirmed (handled in VideoScreen).
        """
        if platform != "android":
            return

        try:
            # Remove the SDL2/presplash as early as possible.
            try:
                from android import remove_presplash  # type: ignore

                Clock.schedule_once(lambda _dt: remove_presplash(), 0)
            except Exception:
                pass

            from android.permissions import Permission, request_permissions  # type: ignore

            perms = [Permission.CAMERA, Permission.RECORD_AUDIO]

            def _on_permissions_result(permissions, grants):
                try:
                    pairs = list(zip(list(permissions or []), list(grants or [])))
                    Logger.info("Permissions: %s", pairs)
                except Exception:
                    Logger.exception("Permissions callback failed")

            request_permissions(perms, _on_permissions_result)
        except Exception:
            # Make startup failures visible in logcat.
            Logger.exception("Permission request failed during on_start()")

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
