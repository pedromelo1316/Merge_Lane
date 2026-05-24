import json
import time

import zenoh

BROKER = "tcp/192.168.98.10:7447"
STATION_IDS = {10: "MC", 11: "A", 12: "B", 13: "C"}

config = zenoh.Config.from_json5(f'{{"mode":"client","connect":{{"endpoints":["{BROKER}"]}}}}')
session = zenoh.open(config)


def _classify_mcm(mcm_type, its_role, inner):
    """Return (label, to_str, success) for an MCM. success is None for non-response messages."""
    vmc = (inner.get("mcmContainer", {})
               .get("vehicleManoeuvreContainer", {}))
    advice = vmc.get("manoeuvreAdvice", [])

    if mcm_type == 1 and its_role == 1:
        return "MERGE_REQUEST", "all", None

    if mcm_type == 1 and its_role == 3:
        target_id = advice[0].get("executantID") if advice else None
        return "SLOWDOWN_REQUEST", (STATION_IDS.get(target_id, str(target_id)) if target_id is not None else "?"), None

    if mcm_type == 2 and its_role == 3:
        resp = (inner.get("mcmContainer", {})
                     .get("responseContainer", {})
                     .get("manouevreResponse", -1))
        success = (resp == 0)
        result = "OK" if success else "ABORT"
        mid = inner.get("basicContainer", {}).get("manoeuvreId", 0)
        if mid >= 128:
            return f"SLOWDOWN_GRANT({result})", None, success
        return f"MERGE_GRANT({result})", "MC", success

    if mcm_type == 2 and its_role == 1:
        resp = (inner.get("mcmContainer", {})
                     .get("responseContainer", {})
                     .get("manouevreResponse", -1))
        if resp == 2:
            return "EXECUTION_STATUS(OK)", "all", True
        result = "OK" if resp == 0 else "ABORT"
        return f"MERGE_CONFIRMED({result})", "all", (resp == 0)

    return f"MCM_{mcm_type}_ROLE_{its_role}", None, None


def on_time_mcm(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        sender_name = STATION_IDS.get(station_id, str(station_id))

        fields = payload["fields"]
        inner = fields.get("payload") or fields["mcm"]
        basic = inner["basicContainer"]
        mcm_type = basic["mcmType"]
        its_role = basic.get("itssRole", 0)
        manoeuvre_id = basic["manoeuvreId"]

        label, to_str, success = _classify_mcm(mcm_type, its_role, inner)

        print(f"[time/mcm] {label:<30}  {sender_name} → {to_str or '?'}  (id={manoeuvre_id})")
    except Exception:
        pass


def on_in_mcm(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        sender_name = STATION_IDS.get(station_id, str(station_id))

        inner = payload
        basic = inner["basicContainer"]
        mcm_type = basic["mcmType"]
        its_role = basic.get("itssRole", 0)
        manoeuvre_id = basic["manoeuvreId"]

        label, to_str, success = _classify_mcm(mcm_type, its_role, inner)

        print(f"[in/mcm]   {label:<30}  {sender_name} → {to_str or '?'}  (id={manoeuvre_id})")
    except Exception:
        pass


session.declare_subscriber("vanetza/time/mcm", on_time_mcm)
session.declare_subscriber("vanetza/in/mcm", on_in_mcm)
print(f"A escutar em {BROKER}")
print("  vanetza/time/mcm (eco do Vanetza)")
print("  vanetza/in/mcm (publicação do vehicle.py)")
print("Ctrl+C para sair")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    session.close()
