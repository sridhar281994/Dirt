from threading import Thread
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from frontend_app.utils.api import api_report_user, ApiError

def show_report_popup(reported_user_id: int | None, context: str):
    # Popup content
    content = BoxLayout(orientation='vertical', spacing=10, padding=10)
    
    # Reason Spinner
    reasons = [
        "Nudity / sexual content",
        "Harassment / abuse",
        "Hate / threats",
        "Scam / spam",
        "Minor safety",
        "Other"
    ]
    spinner = Spinner(
        text="Select Reason",
        values=reasons,
        size_hint_y=None,
        height=44
    )
    content.add_widget(spinner)
    
    # Details Input (optional)
    details_input = TextInput(
        hint_text="Additional details (optional)...",
        multiline=True,
        size_hint_y=None,
        height=100
    )
    content.add_widget(details_input)
    
    # Submit Button
    submit_btn = Button(
        text="Submit Report",
        size_hint_y=None,
        height=44,
        background_color=(0.8, 0.2, 0.2, 1)
    )
    content.add_widget(submit_btn)
    
    # Cancel Button
    cancel_btn = Button(
        text="Cancel",
        size_hint_y=None,
        height=44
    )
    content.add_widget(cancel_btn)
    
    popup = Popup(
        title="Report User",
        content=content,
        size_hint=(0.9, 0.6),
        auto_dismiss=False
    )
    
    cancel_btn.bind(on_release=popup.dismiss)
    
    def on_submit(instance):
        reason = spinner.text
        if reason == "Select Reason":
            # Simple error indication
            spinner.background_color = (1, 0, 0, 1)
            return
            
        details = details_input.text
        
        # Disable button to prevent double submit
        submit_btn.disabled = True
        submit_btn.text = "Submitting..."
        
        def work():
            try:
                api_report_user(
                    reported_user_id=reported_user_id,
                    reason=reason,
                    details=details,
                    context=context
                )
                Clock.schedule_once(lambda dt: popup.dismiss(), 0)
                
                # Show success toast/popup
                def show_success(*_):
                    s_pop = Popup(
                        title="Report Sent",
                        content=Label(text="Thank you for reporting."),
                        size_hint=(0.6, 0.3)
                    )
                    s_pop.open()
                    Clock.schedule_once(lambda dt: s_pop.dismiss(), 2)
                Clock.schedule_once(show_success, 0.5)
                
            except ApiError as e:
                def show_err(*_):
                    submit_btn.disabled = False
                    submit_btn.text = "Submit Report"
                    # Show error
                    e_pop = Popup(
                        title="Error",
                        content=Label(text=str(e)),
                        size_hint=(0.6, 0.3)
                    )
                    e_pop.open()
                Clock.schedule_once(show_err, 0)
                
        Thread(target=work, daemon=True).start()

    submit_btn.bind(on_release=on_submit)
    popup.open()
