import asyncio
import json
import time

import websockets
import zenoh

STATION_IDS = {10: "MC", 11: "A", 12: "B", 13: "C"}
ZENOH_BROKER = "tcp/192.168.98.10:7447"

vehicle_states = {}
ws_clients     = set()
MCM_PENDING    = []  # new events since last broadcast, cleared each cycle


def _extract_ref_position(payload):
    # vanetza/out/cam wraps the CAM in fields.cam; vanetza/own/cam may use the raw structure
    try:
        return payload["fields"]["cam"]["camParameters"]["basicContainer"]["referencePosition"]
    except KeyError:
        return payload["camParameters"]["basicContainer"]["referencePosition"]


def on_cam(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        if station_id not in STATION_IDS:
            return
        ref = _extract_ref_position(payload)
        name = STATION_IDS[station_id]
        vehicle_states[name] = {"id": name, "lat": ref["latitude"], "lon": ref["longitude"]}
    except Exception:
        pass

def _classify_mcm(mcm_type, its_role, inner):
    """Return (label, to_str) for an MCM. to_str is None when recipient isn't in the message."""
    vmc = (inner.get("mcmContainer", {})
               .get("vehicleManoeuvreContainer", {}))
    advice = vmc.get("manoeuvreAdvice", [])

    if mcm_type == 1 and its_role == 1:
        targets = [
            STATION_IDS.get(e.get("executantID"), str(e.get("executantID")))
            for e in advice
        ]
        return "MERGE_REQUEST", ", ".join(targets) or "?"

    if mcm_type == 1 and its_role == 3:
        target_id = advice[0].get("executantID") if advice else None
        return "SLOWDOWN_REQUEST", (STATION_IDS.get(target_id, str(target_id)) if target_id is not None else "?")

    if mcm_type == 9:
        return "ACK", None

    if mcm_type == 2 and its_role == 3:
        return "MERGE_GRANT", "MC"

    if mcm_type == 2 and its_role == 1:
        resp = (inner.get("mcmContainer", {})
                     .get("responseContainer", {})
                     .get("manouevreResponse", -1))
        result = "OK" if resp == 0 else "ABORT"
        return f"EXECUTION_STATUS({result})", "all"

    return f"MCM_{mcm_type}_ROLE_{its_role}", None


def on_mcm(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        sender_name = STATION_IDS.get(station_id, str(station_id))

        fields = payload["fields"]
        inner  = fields.get("payload") or fields["mcm"]
        basic = inner["basicContainer"]
        mcm_type = basic["mcmType"]
        its_role = basic.get("itssRole", 0)
        manoeuvre_id = basic["manoeuvreId"]

        label, to_str = _classify_mcm(mcm_type, its_role, inner)

        print(f"[MCM] {label:<22}  {sender_name} → {to_str or '?'}  (manoeuvre_id={manoeuvre_id})")

        event = {
            "ts":           time.strftime("%H:%M:%S"),
            "type":         label,
            "from":         sender_name,
            "to":           to_str,
            "manoeuvre_id": manoeuvre_id,
        }
        MCM_PENDING.append(event)
    except Exception:
        pass


async def ws_handler(ws):
    ws_clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        ws_clients.discard(ws)


async def broadcast_loop():
    start = time.time()
    while True:
        new_events = MCM_PENDING[:]
        del MCM_PENDING[:]
        msg = json.dumps({
            "t":          round(time.time() - start, 2),
            "vehicles":   list(vehicle_states.values()),
            "mcm_events": new_events,
        })
        dead = set()
        for ws in ws_clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        ws_clients.difference_update(dead)
        await asyncio.sleep(0.1)


async def main():
    config_json = f'{{"mode":"client","connect":{{"endpoints":["{ZENOH_BROKER}"]}}}}'
    session = zenoh.open(zenoh.Config.from_json5(config_json))
    session.declare_subscriber("vanetza/out/cam", on_cam)
    session.declare_subscriber("vanetza/time/cam", on_cam)
    session.declare_subscriber("vanetza/out/mcm", on_mcm)
    session.declare_subscriber("vanetza/time/mcm", on_mcm)

    print(f"Bridge ligado a {ZENOH_BROKER}")
    print("WebSocket a ouvir em ws://localhost:8765")
    try:
        async with websockets.serve(ws_handler, "localhost", 8765):
            await broadcast_loop()
    except asyncio.CancelledError:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
