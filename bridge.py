import asyncio
import json
import logging
import time

import websockets

# nc -z probes (used by run.sh to check readiness) open a TCP connection and
# close it immediately without sending data, causing a noisy but harmless EOFError.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
import zenoh

STATION_IDS           = {10: "MC", 11: "A", 12: "B", 13: "C"}
ZENOH_BROKER          = "tcp/192.168.98.10:7447"
COORDINATOR_ZENOH_URL = "tcp/127.0.0.1:7446"

vehicle_states        = {}
ws_clients            = set()
MCM_PENDING           = []  # new events since last broadcast, cleared each cycle
current_roads             = []
current_scenario_name     = ""
current_scenario_vehicles = set()  # IDs dos veículos activos no cenário actual (e.g. {"A","B","C"})

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
        return "MERGE_REQUEST", "all"

    if mcm_type == 1 and its_role == 3:
        target_id = advice[0].get("executantID") if advice else None
        return "SLOWDOWN_REQUEST", (STATION_IDS.get(target_id, str(target_id)) if target_id is not None else "?")

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

        if label == "SLOWDOWN_REQUEST":
            advice = (inner.get("mcmContainer", {})
                          .get("vehicleManoeuvreContainer", {})
                          .get("manoeuvreAdvice", []))
            if advice:
                executant_id = advice[0].get("executantID")
                if executant_id is not None:
                    _slowdown_sent_to[executant_id] = sender_name

        if to_str is None and label == "SLOWDOWN_GRANT":
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


async def connect_zenoh():
    loop = asyncio.get_event_loop()

    def _open_vanetza():
        config = f'{{"mode":"client","connect":{{"endpoints":["{ZENOH_BROKER}"]}}}}'
        s = zenoh.open(zenoh.Config.from_json5(config))
        s.declare_subscriber("vanetza/out/cam", on_cam)
        s.declare_subscriber("vanetza/time/cam", on_cam)
        s.declare_subscriber("vanetza/out/mcm", on_mcm)
        s.declare_subscriber("vanetza/time/mcm", on_mcm)
        return s

    def _open_coord():
        config = f'{{"mode":"client","connect":{{"endpoints":["{COORDINATOR_ZENOH_URL}"]}}}}'
        s = zenoh.open(zenoh.Config.from_json5(config))
        s.declare_subscriber("coordinator/scenario", on_coordinator_scenario)
        return s

    print(f"[bridge] A ligar ao Zenoh ({ZENOH_BROKER} e {COORDINATOR_ZENOH_URL})...")
    session       = await loop.run_in_executor(None, _open_vanetza)
    coord_session = await loop.run_in_executor(None, _open_coord)
    print(f"[bridge] Zenoh ligado.")
    return session, coord_session


async def main():
    print("WebSocket a ouvir em ws://localhost:8765")
    session = coord_session = None
    try:
        async with websockets.serve(ws_handler, "localhost", 8765):
            # Liga ao Zenoh em paralelo com o WebSocket já a aceitar clientes
            session, coord_session = await connect_zenoh()
            await broadcast_loop()
    except asyncio.CancelledError:
        pass
    finally:
        if session:
            session.close()
        if coord_session:
            coord_session.close()


if __name__ == "__main__":
    asyncio.run(main())
