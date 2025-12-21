from threading import Thread

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.properties import BooleanProperty, NumericProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_guest, api_login_request_otp, api_login_verify_otp
from frontend_app.utils.storage import get_remember_me, set_remember_me, set_session


def _safe_text(screen: Screen, wid: str, default: str = "") -> str:
    w = getattr(screen, "ids", {}).get(wid)
    return (w.text or "").strip() if w else default


def _popup(title: str, msg: str) -> None:
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


class LoginScreen(Screen):
    font_scale = NumericProperty(1.0)
    remember_me = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Clock.schedule_once(self._update_font_scale, 0)

    def on_pre_enter(self, *args):
        # Sync checkbox with persisted preference.
        try:
            self.remember_me = bool(get_remember_me())
        except Exception:
            self.remember_me = False

    def on_size(self, *args):
        self._update_font_scale()

    def _update_font_scale(self, *_):
        width = self.width or Window.width or 1
        height = self.height or Window.height or 1
        width_ratio = width / 520.0
        height_ratio = height / 720.0
        self.font_scale = max(0.75, min(1.25, min(width_ratio, height_ratio)))

    # -----------------------
    # Navigation
    # -----------------------
    def go_back(self):
        # No Welcome screen wired in this repo; keep user on login.
        if self.manager and "welcome" in [s.name for s in self.manager.screens]:
            self.manager.current = "welcome"
        # else: no-op

    def open_forgot_password(self):
        if not self.manager:
            return
        fp = self.manager.get_screen("forgot_password")
        if hasattr(fp, "open_from"):
            fp.open_from(source_screen="login", title="Forgot Password")
        self.manager.current = "forgot_password"

    # -----------------------
    # Helpers
    # -----------------------
    def _read_identifier(self) -> str:
        return _safe_text(self, "phone_input")

    def _read_password(self) -> str:
        return _safe_text(self, "password_input")

    def _validate_identifier(self, identifier: str) -> bool:
        if not identifier:
            return False
        identifier = identifier.strip()
        if "@" in identifier and "." in identifier.split("@")[-1]:
            return True
        if identifier.isdigit() and 6 <= len(identifier) <= 15:
            return True
        return len(identifier) >= 3

    # -----------------------
    # Send OTP
    # -----------------------
    def send_otp_to_user(self):
        identifier = self._read_identifier()
        password = self._read_password()

        if not self._validate_identifier(identifier):
            _popup("Error", "Enter a valid registered email or number.")
            return
        if len(password) < 4:
            _popup("Error", "Enter your password.")
            return

        def work():
            # Request OTP - this will validate the password automatically
            try:
                data = api_login_request_otp(identifier=identifier, password=password)
                _popup("Success", data.get("message") or "OTP sent to your email.")
                return
            except ApiError as exc:
                _popup("Error", str(exc))
                return

        Thread(target=work, daemon=True).start()

    # -----------------------
    # Verify OTP + Login
    # -----------------------
    def verify_and_login(self):
        identifier = self._read_identifier()
        password = self._read_password()
        otp = _safe_text(self, "otp_input")

        if not self._validate_identifier(identifier):
            _popup("Error", "Enter a valid registered email or number.")
            return
        if len(password) < 4:
            _popup("Error", "Enter your password.")
            return
        if not otp:
            _popup("Error", "Enter the OTP.")
            return

        def work():
            try:
                data = api_login_verify_otp(identifier=identifier, password=password, otp=otp)
                token = data.get("access_token")
                user = data.get("user") or {}
                if not token:
                    raise ApiError("Login failed.")

                set_remember_me(bool(self.remember_me))
                set_session(token=token, user=user, remember=bool(self.remember_me))

                def after(*_):
                    if self.manager:
                        self.manager.current = "choose"
                    _popup("Success", "Swipe Left/Right to chat.")

                Clock.schedule_once(after, 0)

            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()

    def login_as_guest(self):
        def work():
            try:
                data = api_guest()
                # Guests should not be persisted by default.
                set_remember_me(False)
                set_session(
                    token=(data.get("access_token") or ""),
                    user=(data.get("user") or {}),
                    remember=False,
                )
                Clock.schedule_once(lambda *_: setattr(self.manager, "current", "choose"), 0)
            except ApiError as exc:
                _popup("Error", str(exc))

        Thread(target=work, daemon=True).start()
