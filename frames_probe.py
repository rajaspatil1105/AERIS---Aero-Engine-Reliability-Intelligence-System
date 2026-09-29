import json, requests
B = "http://localhost:8000"
for params in ({"session_id": 71, "limit": 5},
               {"session_id": 71},
               {"session": 71, "limit": 5}):
    try:
        r = requests.get(B + "/frames", params=params, timeout=60)
        t = r.text[:400]
        print(params, "->", r.status_code, t.replace("\n", " ")[:300], "\n")
    except Exception as e:
        print(params, "-> ERR", e)
