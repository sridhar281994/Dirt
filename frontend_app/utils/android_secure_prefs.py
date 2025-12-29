from __future__ import annotations

from typing import Optional

from kivy.utils import platform
from kivy.logger import Logger


class AndroidSecurePrefs:
    """
    Keystore-backed encrypted preferences for Android (best-effort).

    Uses AndroidX Security Crypto (EncryptedSharedPreferences + MasterKey).
    This avoids storing auth tokens in plaintext on-device (production requirement).
    """

    def __init__(self, *, name: str = "buddymeet_secure_prefs") -> None:
        self._name = str(name or "buddymeet_secure_prefs")
        self._prefs = None

    def _ensure(self):
        if self._prefs is not None:
            return self._prefs
        if platform != "android":
            return None

        try:
            from jnius import autoclass  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            if activity is None:
                return None

            # Context
            context = activity.getApplicationContext()

            MasterKeyBuilder = autoclass("androidx.security.crypto.MasterKey$Builder")
            KeyScheme = autoclass("androidx.security.crypto.MasterKey$KeyScheme")
            EncSharedPrefs = autoclass("androidx.security.crypto.EncryptedSharedPreferences")
            PrefKeyEnc = autoclass(
                "androidx.security.crypto.EncryptedSharedPreferences$PrefKeyEncryptionScheme"
            )
            PrefValEnc = autoclass(
                "androidx.security.crypto.EncryptedSharedPreferences$PrefValueEncryptionScheme"
            )

            master_key = (
                MasterKeyBuilder(context)
                .setKeyScheme(KeyScheme.AES256_GCM)
                .build()
            )

            self._prefs = EncSharedPrefs.create(
                context,
                self._name,
                master_key,
                PrefKeyEnc.AES256_SIV,
                PrefValEnc.AES256_GCM,
            )
            return self._prefs
        except Exception:
            Logger.exception("AndroidSecurePrefs init failed (falling back to plaintext store)")
            self._prefs = None
            return None

    def get(self, key: str) -> Optional[str]:
        prefs = self._ensure()
        if prefs is None:
            return None
        try:
            v = prefs.getString(str(key), None)
            if v is None:
                return None
            return str(v)
        except Exception:
            return None

    def put(self, key: str, value: str) -> None:
        prefs = self._ensure()
        if prefs is None:
            return
        try:
            editor = prefs.edit()
            editor.putString(str(key), str(value))
            editor.apply()
        except Exception:
            Logger.exception("AndroidSecurePrefs put failed")

    def delete(self, key: str) -> None:
        prefs = self._ensure()
        if prefs is None:
            return
        try:
            editor = prefs.edit()
            editor.remove(str(key))
            editor.apply()
        except Exception:
            Logger.exception("AndroidSecurePrefs delete failed")

