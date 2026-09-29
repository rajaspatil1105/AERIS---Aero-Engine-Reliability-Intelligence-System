import requests
B, S = "http://localhost:8000", "RTX915-0002"
for h in (100, 300, 1000):
    requests.post(B + "/sim/age/reset", params={"serial": S}, timeout=30)
    requests.post(B + "/sim/age", params={"serial": S, "hours": h}, timeout=300)
    e = [x for x in requests.get(B + "/sim/fleet", timeout=30).json()["engines"]
         if x["serial"] == S][0]
    print("%5d h ->  oil %.4f  coolant %.4f  bearing %.4f"
          % (h, e["oil_pump_health"], e["coolant_pump_health"], e["bearing_wear"]))
requests.post(B + "/sim/age/reset", params={"serial": S}, timeout=30)
print("reset")
