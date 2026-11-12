import requests
import datetime
import logging

def fetch_user_data(user_id):
    response = requests.get(f"https://api.example.com/users/{user_id}")
    
    now = datetime.datetime.now()
    
    logging.info(f"Fetched data at {now}")
    
    return response.json()