import asyncio
import json
import time

import websockets
import zenoh

STATION_IDS           = {10: "MC", 11: "A", 12: "B", 13: "C", 14: "D",15: "E", 16: "F", 17:"MC2"}
ZENOH_BROKER          = "tcp/192.168.98.10:7447"
COORDINATOR_ZENOH_URL = "tcp/127.0.0.1:7446"

vehicle_states        = {}
ws_clients            = set()
MCM_PENDING           = []  # new events since last broadcast, cleared each cycle
current_roads             = []
current_scenario_name     = ""
current_scenario_vehicles = set()  # IDs dos veículos activos no cenário actual (e.g. {"A","B","C"})

# Recipient resolution history (Opção 2 — derivar destinatário pelo contexto)
_msg_by_delta     = {}  # {generationDeltaTime → sender_name}  — para ACKs
_slowdown_sent_to = {}  # {executant_station_id → sender_name} — para SLOWDOWN_GRANTs


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
        mid = inner.get("basicContainer", {}).get("manoeuvreId", 0)
        if mid >= 128:
            return "SLOWDOWN_GRANT", None
        return "MERGE_GRANT", "MC"

    if mcm_type == 2 and its_role == 1:
        resp = (inner.get("mcmContainer", {})
                     .get("responseContainer", {})
                     .get("manouevreResponse", -1))
        result = "OK" if resp == 0 else "ABORT"
        return f"EXECUTION_STATUS({result})", "all"

    return f"MCM_{mcm_type}_ROLE_{its_role}", None


def on_coordinator_scenario(sample):
    global current_roads, current_scenario_name, current_scenario_vehicles
    try:
        data = json.loads(bytes(sample.payload).decode())
        current_roads             = data.get("roads", [])
        current_scenario_name     = data.get("name", "")
        current_scenario_vehicles = {v["id"] for v in data.get("vehicles", [])}

        vehicle_states.clear()
        del MCM_PENDING[:]
        _msg_by_delta.clear()
        _slowdown_sent_to.clear()

        print(f"[bridge] Cenário recebido: {current_scenario_name!r} ({len(current_roads)} estradas, veículos: {current_scenario_vehicles})")
    except Exception:
        pass


def on_mcm(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        sender_name = STATION_IDS.get(station_id, str(station_id))

        fields = payload["fields"]
        inner  = fields.get("payload") or fields["mcm"]
        basic = inner["basicContainer"]
        mcm_type     = basic["mcmType"]
        its_role     = basic.get("itssRole", 0)
        manoeuvre_id = basic["manoeuvreId"]
        delta_time   = basic["generationDeltaTime"]

        label, to_str = _classify_mcm(mcm_type, its_role, inner)

        # Normalize to integer milliseconds so vanetza/time/mcm (original float, full precision)
        # and vanetza/out/mcm (decoded, sub-ms precision lost) produce the same key.
        # e.g. 1741192835.4648783 and 1741192835.464 both → int key 1741192835464
        _msg_by_delta[int(delta_time * 1000)] = sender_name

        if label == "SLOWDOWN_REQUEST":
            advice = (inner.get("mcmContainer", {})
                          .get("vehicleManoeuvreContainer", {})
                          .get("manoeuvreAdvice", []))
            if advice:
                executant_id = advice[0].get("executantID")
                if executant_id is not None:
                    _slowdown_sent_to[executant_id] = sender_name

        if to_str is None:
            if label == "ACK":
                ack_delta = (inner.get("mcmContainer", {})
                                  .get("acknowledgmentContainer", {})
                                  .get("generationDeltaTime"))
                if ack_delta is not None:
                    to_str = _msg_by_delta.get(int(ack_delta * 1000), "?")
            elif label == "SLOWDOWN_GRANT":
                to_str = _slowdown_sent_to.get(station_id, "?")

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
            "scenario":   current_scenario_name,
            "roads":      current_roads,
            "vehicles":   [v for v in vehicle_states.values()
                           if not current_scenario_vehicles or v["id"] in current_scenario_vehicles],
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
    config_vanetza = f'{{"mode":"client","connect":{{"endpoints":["{ZENOH_BROKER}"]}}}}'
    session = zenoh.open(zenoh.Config.from_json5(config_vanetza))
    session.declare_subscriber("vanetza/out/cam", on_cam)
    session.declare_subscriber("vanetza/time/cam", on_cam)
    session.declare_subscriber("vanetza/out/mcm", on_mcm)
    session.declare_subscriber("vanetza/time/mcm", on_mcm)

    config_coord = f'{{"mode":"client","connect":{{"endpoints":["{COORDINATOR_ZENOH_URL}"]}}}}'
    coord_session = zenoh.open(zenoh.Config.from_json5(config_coord))
    coord_session.declare_subscriber("coordinator/scenario", on_coordinator_scenario)

    print(f"Bridge ligado a {ZENOH_BROKER} (V2X) e {COORDINATOR_ZENOH_URL} (coordinator)")
    print("WebSocket a ouvir em ws://localhost:8765")
    try:
        async with websockets.serve(ws_handler, "localhost", 8765):
            await broadcast_loop()
    except asyncio.CancelledError:
        pass
    finally:
        session.close()
        coord_session.close()


if __name__ == "__main__":
    asyncio.run(main())
