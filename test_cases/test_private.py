import requests
import datetime
import threading

def process_user_data(user_data):
    response = requests.get("https://api.example.com/data", timeout=5)
    now = datetime.datetime.now()
    user_id = user_data["id"]
    
    t = threading.Thread(target=lambda: print("hello"))
    t.start()
    
    return response.json()