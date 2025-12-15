# main.py
# Kivy skeleton for chat/video app with login, register, choose screen
# NOTE: This is a STRUCTURAL starter. OTP, Google/Facebook login,
# subscriptions, video, and backend APIs MUST be implemented server-side.

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty
from kivy.clock import Clock

KV = '''
ScreenManager:
    LoginScreen:
    RegisterScreen:
    ChooseScreen:

<LoginScreen>:
    name: 'login'
    BoxLayout:
        orientation: 'vertical'
        padding: 20
        spacing: 10

        Label:
            text: 'Login'
            font_size: '22sp'

        TextInput:
            id: email
            hint_text: 'Email / Username'
            multiline: False

        TextInput:
            id: password
            hint_text: 'Password'
            password: True
            multiline: False

        Button:
            text: 'Send OTP'
            on_release: root.send_otp()

        TextInput:
            id: otp
            hint_text: 'Enter OTP'
            multiline: False

        Button:
            text: 'Verify & Login'
            on_release: root.verify_login()

        Button:
            text: 'Login as Guest'
            on_release: app.root.current = 'choose'

        Button:
            text: 'Go to Register'
            on_release: app.root.current = 'register'

<RegisterScreen>:
    name: 'register'
    BoxLayout:
        orientation: 'vertical'
        padding: 20
        spacing: 10

        Label:
            text: 'Register'
            font_size: '22sp'

        Button:
            text: 'Login via Gmail'
            on_release: root.social_login('gmail')

        Button:
            text: 'Login via Facebook / Instagram'
            on_release: root.social_login('meta')

        Spinner:
            id: country
            text: 'Select Country'
            values: root.country_list

        Spinner:
            id: gender
            text: 'Select Gender'
            values: ['Male', 'Female', 'Cross']

        Button:
            text: 'Register'
            on_release: root.register_user()

        Button:
            text: 'Back to Login'
            on_release: app.root.current = 'login'

<ChooseScreen>:
    name: 'choose'
    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: '50dp'

            Spinner:
                id: preference
                text: 'Preference'
                values: ['Male', 'Female', 'Both']
                on_text: root.on_preference(self.text)

            Button:
                text: 'Subscribe'
                on_release: root.open_subscription()

            Button:
                text: 'Logout'
                on_release: root.logout()

        BoxLayout:
            padding: 20
            spacing: 20

            Button:
                text: 'Text Chat'
                on_release: root.start_chat('text')

            Button:
                text: 'Video Chat'
                on_release: root.start_chat('video')
'''

class LoginScreen(Screen):
    def send_otp(self):
        # Call backend API to send OTP
        print('OTP sent')

    def verify_login(self):
        # Verify OTP from backend
        self.manager.current = 'choose'

class RegisterScreen(Screen):
    country_list = [
        'India', 'USA', 'UK', 'Canada', 'Australia', 'Germany', 'France'
    ]

    def social_login(self, provider):
        print(f'Social login: {provider}')

    def register_user(self):
        print('User registered')
        self.manager.current = 'choose'

class ChooseScreen(Screen):
    user_gender = StringProperty('Male')
    preference = StringProperty('Both')

    def on_preference(self, value):
        # BLOCK opposite gender unless subscribed
        if not self.is_allowed(value):
            print('Subscription required')
            return
        self.preference = value

    def is_allowed(self, value):
        # Replace with real subscription check
        subscribed = False
        if value != 'Both' and not subscribed:
            return False
        return True

    def start_chat(self, mode):
        print(f'Start {mode} chat')
        # Matchmaking + billing logic here

    def open_subscription(self):
        print('Open Google Play Subscription')

    def logout(self):
        self.manager.current = 'login'

class ChatApp(App):
    def build(self):
        return Builder.load_string(KV)

if __name__ == '__main__':
    ChatApp().run()

