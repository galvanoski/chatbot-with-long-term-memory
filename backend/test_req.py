import urllib.request
import json

# Use the actual user_id from the DB
url = 'http://localhost:8000/api/chat/threads/22524d77-71a3-4a05-b5d2-4106c8f12bb9?user_id=anon_948a09d9-0e47-48a8-8aa6-e44e75761e9c'
try:
    r = urllib.request.urlopen(url)
    data = json.loads(r.read().decode())
    print("Status:", r.status)
    print("Keys:", list(data.keys()))
    print("Messages:", len(data.get('messages', [])))
    print("Status field:", data.get('status'))
except urllib.error.HTTPError as e:
    print(f"HTTP Error: {e.code}")
    print(f"Body: {e.read().decode()[:500]}")
except Exception as e:
    print(f"Error: {e}")
