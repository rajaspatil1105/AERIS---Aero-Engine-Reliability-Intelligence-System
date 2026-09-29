import requests, json
B = "http://localhost:8000"
S = "RTX915-0007"

def show(tag):
    j = requests.get(B + "/sim/fleet", timeout=30).json()
    e = [x for x in j["engines"] if x["serial"] == S][0]
    print("%-10s %6.0f h  oil %.4f  coolant %.4f  bearing %.4f"
          % (tag, e["hours"], e["oil_pump_health"],
             e["coolant_pump_health"], e["bearing_wear"]))

show("before")
r = requests.post(B + "/sim/age", params={"serial": S, "hours": 300}, timeout=120)
print(r.status_code)
print(json.dumps(r.json().get("cost_per_hour", r.json()), indent=1))
show("after")

print(requests.post(B + "/sim/age/reset", params={"serial": S}, timeout=30).json())
show("reset")
