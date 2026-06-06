import json
import time

import zenoh

BROKER = "tcp/192.168.98.13:7447"
STATION_IDS = {10: "MC", 11: "A", 12: "B", 13: "C"}

config = zenoh.Config.from_json5(
    f'{{"mode":"client","connect":{{"endpoints":["{BROKER}"]}}}}'
)
session = zenoh.open(config)


def _classify_mcm(mcm_type, its_role, inner):
    """Return (label, to_str) for an MCM."""
    vmc = inner.get("mcmContainer", {}).get("vehicleManoeuvreContainer", {})
    advice = vmc.get("manoeuvreAdvice", [])

    if mcm_type == 1 and its_role == 1:
        return "MERGE_REQUEST", "all"

    if mcm_type == 1 and its_role == 3:
        target_id = advice[0].get("executantID") if advice else None
        return "SLOWDOWN_REQUEST", (
            STATION_IDS.get(target_id, str(target_id)) if target_id else "?"
        )

    if mcm_type == 2 and its_role == 3:
        resp = inner.get("mcmContainer", {}).get("responseContainer", {}).get("manouevreResponse", -1)
        result = "OK" if resp == 0 else "ABORT"
        mid = inner.get("basicContainer", {}).get("manoeuvreId", 0)
        if mid >= 128:
            return f"SLOWDOWN_GRANT({result})", None
        return f"MERGE_GRANT({result})", "MC"

    if mcm_type == 2 and its_role == 1:
        resp = inner.get("mcmContainer", {}).get("responseContainer", {}).get("manouevreResponse", -1)
        if resp == 2:
            return "EXECUTION_STATUS(OK)", "all"
        result = "OK" if resp == 0 else "ABORT"
        return f"MERGE_CONFIRMED({result})", "all"

    return f"MCM_{mcm_type}_ROLE_{its_role}", None


def on_in_mcm(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        sender_name = STATION_IDS.get(station_id, str(station_id))

        basic = payload["basicContainer"]
        mcm_type = basic["mcmType"]
        its_role = basic.get("itssRole", 0)
        manoeuvre_id = basic["manoeuvreId"]

        label, to_str = _classify_mcm(mcm_type, its_role, payload)
        print(f"\n[in/mcm] {label:<30} {sender_name} → {to_str or '?'}  (id={manoeuvre_id})")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("-" * 80)
    except Exception:
        pass


session.declare_subscriber("vanetza/in/mcm", on_in_mcm)
print(f"A escutar em {BROKER}")
print("  vanetza/in/mcm (publicação do vehicle.py)")
print("Ctrl+C para sair")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    session.close()