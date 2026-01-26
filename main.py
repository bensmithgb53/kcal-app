import threading
import time
import webbrowser
from kivy.app import App
from kivy.uix.label import Label
from kivy.clock import Clock
from app import app

class NutritionLoader(App):
    def build(self):
        self.server_thread = threading.Thread(target=self.run_flask)
        self.server_thread.daemon = True
        self.server_thread.start()
        Clock.schedule_once(self.open_browser, 2)
        return Label(text="Loading Nutrition App...", halign='center')

    def run_flask(self):
        app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

    def open_browser(self, dt):
        webbrowser.open('http://127.0.0.1:5000')

if __name__ == '__main__':
    NutritionLoader().run()

