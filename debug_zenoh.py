import json
import time

import zenoh

BROKER = "tcp/192.168.98.11:7447"

config = zenoh.Config.from_json5(f'{{"mode":"client","connect":{{"endpoints":["{BROKER}"]}}}}')
session = zenoh.open(config)


def on_sample(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId") or "?"
        print(f"[{sample.key_expr}] stationID={station_id}")
        print(json.dumps(payload, indent=2))
    except Exception:
        print(f"[{sample.key_expr}] raw={bytes(sample.payload)[:80]}")


session.declare_subscriber("vanetza/**", on_sample)
print(f"A escutar em {BROKER} — vanetza/**")
print("Ctrl+C para sair")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    session.close()
