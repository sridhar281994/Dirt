from threading import Thread
import re

from kivy.clock import Clock
from kivy.uix.popup import Popup
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen

from frontend_app.utils.api import ApiError, api_register
from frontend_app.utils.countries import COUNTRIES


class RegisterScreen(Screen):
    @staticmethod
    def _popup(title: str, msg: str) -> None:
        """Non-blocking popup helper (safe from worker threads)."""

        def _open(*_):
            Popup(
                title=title,
                content=Label(text=msg),
                size_hint=(0.7, 0.3),
                auto_dismiss=True,
            ).open()

        Clock.schedule_once(_open, 0)

    def go_back(self) -> None:
        """Navigate back to login or stage screen."""
        self.manager.current = "login"

    def _get(self, wid: str) -> str:
        """Helper to get and trim text from widget by ID."""
        w = self.ids.get(wid)
        return (w.text or "").strip() if w else ""

    def _validate(self, phone: str, email: str, password: str) -> str | None:
        """Basic validation for registration fields."""
        if not EMAIL_RE.match(email):
            return "Enter a valid email address."
        if len(password) < 6:
            return "Password must be at least 6 characters."
        return None

    def save_profile(self) -> None:
        """Collects input, validates, and calls register API."""
        name = self._get("name_input")
        username = self._get("phone_input")  # reusing existing field id as "username"
        email = self._get("email_input")
        password = self._get("password_input")
        country = (self.ids.get("country_spinner").text or "").strip() if self.ids.get("country_spinner") else ""
        gender = (self.ids.get("gender_spinner").text or "").strip() if self.ids.get("gender_spinner") else ""

        err = self._validate(username, email, password)
        if err:
            self._popup("Invalid", err)
            return

        if not name:
            self._popup("Invalid", "Please enter your full name.")
            return
        if not username or len(username) < 3:
            self._popup("Invalid", "Please enter a username (min 3 chars).")
            return
        if not country or country == "Select Country":
            self._popup("Invalid", "Please select your country.")
            return
        if gender.lower() not in {"male", "female", "cross"}:
            self._popup("Invalid", "Please select gender: male/female/cross.")
            return

        def work():
            try:
                res = api_register(
                    email=email,
                    username=username,
                    password=password,
                    name=name.strip(),
                    country=country,
                    gender=gender.lower(),
                )

                if res.get("ok"):
                    def done_ok(*_):
                        self._popup("Success", "Registered successfully. Please login.")
                        self.manager.current = "login"
                    Clock.schedule_once(done_ok, 0)
                else:
                    msg = res.get("detail") or res.get("message") or "Registration failed."
                    self._popup("Error", msg)

            except ApiError as e:
                self._popup("Error", str(e))

        Thread(target=work, daemon=True).start()

    def social_login(self, provider: str) -> None:
        self._popup("Info", f"{provider} login will be added later.")

    def country_values(self):
        return COUNTRIES
