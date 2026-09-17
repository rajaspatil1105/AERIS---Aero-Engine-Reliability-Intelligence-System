import requests, json
B = "http://localhost:8000"
SID = 21
rows = requests.get(B + "/frames", params={"session_id": SID, "limit": 1000},
                    timeout=30).json()
rows = rows.get("frames", rows) if isinstance(rows, dict) else rows
print("list-row keys:", sorted(rows[0].keys()))
fid = rows[0].get("id") or rows[0].get("frame_id")
d = requests.get("%s/frames/%d" % (B, fid), params={"session_id": SID},
                 timeout=10).json()
print("detail keys:", sorted(d.keys()))
print(json.dumps(d, indent=2, default=str)[:2500])
