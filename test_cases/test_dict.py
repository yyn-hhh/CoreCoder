import requests  
from xingyu_safe_dict import safe_get

def get_user_age(user_data):
    age = user_data.get("age", 0) 
    return age