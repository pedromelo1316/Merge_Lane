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
vehicle_protocol_states = {}  # {name → "NORMAL"|"SLOWING"|"SPEEDING"|"MERGING"}
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

        # Inferir estado físico (SLOWING/SPEEDING) a partir da aceleração longitudinal nas CAMs
        # Se o estado atual é MERGING (protocolo override), manter sem alterar
        hfc = (payload.get("fields", {})
                      .get("cam", {})
                      .get("camParameters", {})
                      .get("highFrequencyContainer", {})
                      .get("basicVehicleContainerHighFrequency", {}))
        accel = hfc.get("longitudinalAcceleration", {}).get("value", 161)

        ACCEL_UNAVAILABLE = 161
        current = vehicle_protocol_states.get(name, "NORMAL")
        if current != "MERGING" and accel != ACCEL_UNAVAILABLE:
            if accel < 0:
                vehicle_protocol_states[name] = "SLOWING"
            elif accel > 0:
                vehicle_protocol_states[name] = "SPEEDING"
            else:
                vehicle_protocol_states[name] = "NORMAL"
    except Exception:
        pass

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

    if mcm_type == 7 and its_role == 1:
        return "EXECUTION_STATUS(OK)", "all", True

    if mcm_type == 2 and its_role == 1:
        resp = (inner.get("mcmContainer", {})
                     .get("responseContainer", {})
                     .get("manouevreResponse", -1))
        result = "OK" if resp == 0 else "ABORT"
        return f"MERGE_CONFIRMED({result})", "all", (resp == 0)

    return f"MCM_{mcm_type}_ROLE_{its_role}", None, None


def on_coordinator_scenario(sample):
    global current_roads, current_scenario_name, current_scenario_vehicles
    try:
        data = json.loads(bytes(sample.payload).decode())
        current_roads             = data.get("roads", [])
        current_scenario_name     = data.get("name", "")
        current_scenario_vehicles = {v["id"] for v in data.get("vehicles", [])}

        vehicle_states.clear()
        vehicle_protocol_states.clear()
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

        label, to_str, success = _classify_mcm(mcm_type, its_role, inner)

        if label.startswith("SLOWDOWN_REQUEST"):
            advice = (inner.get("mcmContainer", {})
                          .get("vehicleManoeuvreContainer", {})
                          .get("manoeuvreAdvice", []))
            if advice:
                executant_id = advice[0].get("executantID")
                if executant_id is not None:
                    _slowdown_sent_to[executant_id] = sender_name

        if to_str is None and label.startswith("SLOWDOWN_GRANT"):
            to_str = _slowdown_sent_to.get(station_id, "?")

        print(f"[MCM] {label:<30}  {sender_name} → {to_str or '?'}  (manoeuvre_id={manoeuvre_id})")

        if label.startswith("MERGE_CONFIRMED") and success:
            vehicle_protocol_states["MC"] = "MERGING"
        elif label.startswith("EXECUTION_STATUS"):
            vehicle_protocol_states["MC"] = "NORMAL"

        # Extract additional fields for modal enrichment
        extras = {}
        vmc = inner.get("mcmContainer", {}).get("vehicleManoeuvreContainer", {})

        if label == "MERGE_REQUEST":
            submaneuvres = vmc.get("submaneuvres", [{}])
            if submaneuvres:
                tc = submaneuvres[0].get("temporalCharateristics", {})
                extras["eta_start_ms"] = tc.get("tRROccupancyStartTime")
                extras["eta_end_ms"]   = tc.get("tRROccupancyEndTime")
            pos = basic.get("position", {})
            extras["entry_lat"] = pos.get("latitude")
            extras["entry_lon"] = pos.get("longitude")

        elif label.startswith("SLOWDOWN_REQUEST"):
            advice = vmc.get("manoeuvreAdvice", [{}])
            if advice:
                subs = advice[0].get("submaneuvres", [{}])
                if subs:
                    speeds = subs[0].get("advisedTrajectory", {}).get("speed", [{}])
                    if speeds:
                        speed_ms = speeds[0].get("speedValue", 0)
                        extras["target_speed_kmh"] = round(speed_ms * 3.6, 1)

        event = {
            "ts":           time.strftime("%H:%M:%S"),
            "type":         label,
            "from":         sender_name,
            "to":           to_str,
            "manoeuvre_id": manoeuvre_id,
            "success":      success,
            **extras,
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
            "vehicles":   [{**v, "state": vehicle_protocol_states.get(v["id"], "NORMAL")}
                           for v in vehicle_states.values()
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


_zenoh_sessions: list = []


def _open_zenoh_sync() -> tuple:
    """Blocking: opens both Zenoh sessions and registers all subscribers."""
    config_vanetza = f'{{"mode":"client","connect":{{"endpoints":["{ZENOH_BROKER}"]}}}}'
    s = zenoh.open(zenoh.Config.from_json5(config_vanetza))
    s.declare_subscriber("vanetza/out/cam", on_cam)
    s.declare_subscriber("vanetza/time/cam", on_cam)
    s.declare_subscriber("vanetza/out/mcm", on_mcm)
    s.declare_subscriber("vanetza/time/mcm", on_mcm)

    config_coord = f'{{"mode":"client","connect":{{"endpoints":["{COORDINATOR_ZENOH_URL}"]}}}}'
    c = zenoh.open(zenoh.Config.from_json5(config_coord))
    c.declare_subscriber("coordinator/scenario", on_coordinator_scenario)
    return s, c


async def _zenoh_background():
    """Connect to Zenoh in the background so the WebSocket is immediately usable."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            print(f"[bridge] A ligar ao Zenoh ({ZENOH_BROKER} e {COORDINATOR_ZENOH_URL})...")
            s, c = await loop.run_in_executor(None, _open_zenoh_sync)
            _zenoh_sessions.extend([s, c])
            print("[bridge] Zenoh ligado.")
            return
        except Exception as exc:
            print(f"[bridge] Zenoh falhou ({exc}). Nova tentativa em 5s...")
            await asyncio.sleep(5)


async def main():
    print("WebSocket a ouvir em ws://localhost:8765")
    try:
        async with websockets.serve(ws_handler, "localhost", 8765):
            asyncio.create_task(_zenoh_background())
            await broadcast_loop()
    except asyncio.CancelledError:
        pass
    finally:
        for s in _zenoh_sessions:
            try:
                s.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
