from __future__ import annotations

from threading import Thread
from typing import Any, Optional

from kivy.clock import Clock
from kivy.properties import BooleanProperty, StringProperty
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_forgot_password_request_otp, api_forgot_password_reset


def _safe_text(screen: Screen, wid: str) -> str:
    widget = screen.ids.get(wid)
    return (widget.text or "").strip() if widget else ""


def _popup(title: str, msg: str) -> None:
    def _open(*_):
        popup = Popup(
            title=title,
            content=Label(text=str(msg)),
            size_hint=(0.75, 0.35),
            auto_dismiss=True,
        )
        popup.open()
        Clock.schedule_once(lambda _dt: popup.dismiss(), 2.5)

    Clock.schedule_once(_open, 0)


class ForgotPasswordScreen(Screen):
    """
    Step 1: Enter email/phone.
    Step 2: Enter OTP.
    Then navigate to ResetPasswordScreen.
    """
    otp_stage_ready = BooleanProperty(False)

    def go_back(self) -> None:
        if self.manager:
            self.manager.current = "login"

    def send_reset_otp(self) -> None:
        identifier = _safe_text(self, "phone_input")
        if not identifier:
            _popup("Error", "Enter your email or username.")
            return
        
        def work():
            try:
                api_forgot_password_request_otp(identifier=identifier)
                def ok(*_):
                    self.otp_stage_ready = True
                    _popup("Success", "OTP sent.")
                Clock.schedule_once(ok, 0)
            except ApiError as e:
                _popup("Error", str(e))
        
        Thread(target=work, daemon=True).start()

    def verify_otp_and_continue(self) -> None:
        otp = _safe_text(self, "otp_input")
        identifier = _safe_text(self, "phone_input")
        
        if not otp:
            _popup("Error", "Enter OTP.")
            return

        if self.manager:
            reset_screen = self.manager.get_screen("reset_password")
            reset_screen.set_context(identifier=identifier, otp=otp)
            self.manager.current = "reset_password"


class ResetPasswordScreen(Screen):
    """Step 3: Enter new password."""
    current_identifier = StringProperty("")
    current_otp = StringProperty("")
    is_processing = BooleanProperty(False)

    def go_back(self) -> None:
        if self.manager:
            self.manager.current = "forgot_password"

    def set_context(self, identifier: str, otp: str) -> None:
        self.current_identifier = identifier
        self.current_otp = otp

    def save_new_password(self) -> None:
        if self.is_processing:
            return
            
        password = _safe_text(self, "password_input")
        confirm = _safe_text(self, "confirm_password_input")

        if len(password) < 6:
            _popup("Invalid", "Password must be at least 6 characters.")
            return
        if password != confirm:
            _popup("Invalid", "Passwords do not match.")
            return

        def work():
            self.is_processing = True
            try:
                api_forgot_password_reset(
                    identifier=self.current_identifier,
                    otp=self.current_otp,
                    new_password=password
                )
                
                def after(*_):
                    _popup("Success", "Password updated.")
                    if self.manager:
                        self.manager.current = "login"
                Clock.schedule_once(after, 0)
                
            except ApiError as e:
                _popup("Error", str(e))
            finally:
                self.is_processing = False

        Thread(target=work, daemon=True).start()
