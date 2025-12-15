from __future__ import annotations

from kivy.properties import NumericProperty
from kivy.uix.screenmanager import Screen


class VideoScreen(Screen):
    session_id = NumericProperty(0)

    def set_session(self, *, session_id: int):
        self.session_id = int(session_id)

    def go_back(self):
        if self.manager:
            self.manager.current = "choose"

