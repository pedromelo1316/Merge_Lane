import asyncio
import json
import logging
import math
import time

import websockets

# nc -z probes (used by run.sh to check readiness) open a TCP connection and
# close it immediately without sending data, causing a noisy but harmless EOFError.
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
import zenoh

STATION_IDS_BASE      = {10: "MC", 11: "A", 12: "B", 13: "C"}
STATION_IDS           = dict(STATION_IDS_BASE)
ZENOH_BROKER          = "tcp/192.168.98.10:7447"
COORDINATOR_ZENOH_URL = "tcp/127.0.0.1:7446"

vehicle_states        = {}
vehicle_last_cam      = {}   # {name → time.time()} — timestamp do último CAM recebido
vehicle_protocol_states = {}  # {name → "NORMAL"|"SLOWING"|"SPEEDING"|"MERGING"}
vehicle_lengths_from_scenario = {}  # {name → length_m} — carregado do cenário
vehicle_widths_from_scenario  = {}  # {name → width_m}  — carregado do cenário

CAM_TIMEOUT_S = 0.2  # remover veículo da dashboard após este silêncio
ws_clients            = set()
MCM_PENDING           = []  # new events since last broadcast, cleared each cycle
current_roads             = []
current_scenario_name     = ""
current_scenario_vehicles = set()  # IDs dos veículos activos no cenário actual (e.g. {"A","B","C"})
conflict_zone             = None   # {start:{lat,lon}, end:{lat,lon}} ou None
merge_point               = None   # {lat, lon} — interseção geométrica main ∩ ramp
scenario_reset_counter    = 0      # incrementa em cada reset de cenário
draw_exag                 = 3      # fator de exageração visual; controlado pelo coordinator

_slowdown_sent_to = {}  # {executant_station_id → sender_name} — para SLOWDOWN_GRANTs
_merge_request_by_mid = {}  # {manoeuvre_id → (sender_name, ts)}
MERGE_REQUEST_TTL_S = 5.0


def _compute_merge_point(roads):
    """Interseção geométrica entre a estrada principal e a rampa (linha infinita × linha infinita)."""
    main = next((r for r in roads if r.get("type") == "main"), None)
    ramp = next((r for r in roads if r.get("type") == "ramp"), None)
    if not main or not ramp:
        return None
    ax, ay = main["start"]["lon"], main["start"]["lat"]
    bx, by = main["end"]["lon"],   main["end"]["lat"]
    cx, cy = ramp["start"]["lon"], ramp["start"]["lat"]
    dx, dy = ramp["end"]["lon"],   ramp["end"]["lat"]
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    rxs = rx * sy - ry * sx
    if abs(rxs) < 1e-15:
        return None  # paralelas
    t = ((cx - ax) * sy - (cy - ay) * sx) / rxs
    return {"lat": ay + t * ry, "lon": ax + t * rx}


def _nearest_road_id(lat, lon):
    best_id, best_dist = None, math.inf
    for road in current_roads:
        ax, ay = road["start"]["lon"], road["start"]["lat"]
        bx, by = road["end"]["lon"],   road["end"]["lat"]
        abx, aby = bx - ax, by - ay
        t = max(0.0, min(1.0, ((lon-ax)*abx + (lat-ay)*aby) / (abx**2 + aby**2 + 1e-15)))
        dist = math.hypot(lon - (ax + t*abx), lat - (ay + t*aby))
        if dist < best_dist:
            best_dist, best_id = dist, road.get("id", "?")
    return best_id or "?"


def _road_by_id(road_id):
    for road in current_roads:
        if road.get("id") == road_id:
            return road
    return None


def _vehicle_role(state):
    road = _road_by_id(state.get("road_id"))
    if road and road.get("type") == "ramp" and road.get("merges_into"):
        return "MC"
    return "ROAD"


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
        ref = _extract_ref_position(payload)
        name = STATION_IDS.get(station_id, str(station_id))

        # Extract speed from high-frequency container
        hfc = (payload.get("fields", {})
                      .get("cam", {})
                      .get("camParameters", {})
                      .get("highFrequencyContainer", {})
                      .get("basicVehicleContainerHighFrequency", {}))
        speed_ms = hfc.get("speed", {}).get("speedValue", 0)
        speed_kmh = round(speed_ms * 3.6, 1)

        # Extract acceleration (already in m/s²)
        ACCEL_UNAVAILABLE = 161
        accel_raw = hfc.get("longitudinalAcceleration", {}).get("value", ACCEL_UNAVAILABLE)
        accel_ms2 = round(accel_raw, 1) if accel_raw != ACCEL_UNAVAILABLE else None

        # Vehicle dimensions come from the scenario definition; fall back to defaults if unknown
        length_m = vehicle_lengths_from_scenario.get(name, 4.5)
        width_m  = vehicle_widths_from_scenario.get(name, 1.8)

        # Find nearest road
        road_id = _nearest_road_id(ref["latitude"], ref["longitude"])

        vehicle_last_cam[name] = time.time()
        vehicle_states[name] = {
            "id": name,
            "lat": ref["latitude"],
            "lon": ref["longitude"],
            "speed_kmh": speed_kmh,
            "accel_ms2": accel_ms2,
            "road_id": road_id,
            "length_m": length_m,
            "width_m":  width_m,
        }

        # Inferir estado físico (SLOWING/SPEEDING) a partir da aceleração longitudinal nas CAMs
        # Se o estado atual é MERGING (protocolo override), manter sem alterar
        current = vehicle_protocol_states.get(name, "NORMAL")
        if current != "MERGING" and accel_raw != ACCEL_UNAVAILABLE:
            if accel_raw < 0:
                vehicle_protocol_states[name] = "SLOWING"
            elif accel_raw > 0:
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
    global current_roads, current_scenario_name, current_scenario_vehicles, STATION_IDS, conflict_zone, merge_point, scenario_reset_counter, draw_exag
    try:
        data = json.loads(bytes(sample.payload).decode())
        current_roads             = data.get("roads", [])
        current_scenario_name     = data.get("name", "")
        draw_exag                 = int(data.get("draw_exag", 3))
        current_scenario_vehicles = {v["id"] for v in data.get("vehicles", [])}

        vehicle_states.clear()
        vehicle_last_cam.clear()
        vehicle_protocol_states.clear()
        vehicle_lengths_from_scenario.clear()
        vehicle_widths_from_scenario.clear()
        del MCM_PENDING[:]

        STATION_IDS = dict(STATION_IDS_BASE)
        for v in data.get("vehicles", []):
            sid = v.get("station_id")
            vid = v.get("id")
            if sid is not None and vid is not None:
                STATION_IDS[sid] = vid
                if "length_m" in v:
                    vehicle_lengths_from_scenario[vid] = v["length_m"]
                if "width_m" in v:
                    vehicle_widths_from_scenario[vid] = v["width_m"]
        _slowdown_sent_to.clear()
        _merge_request_by_mid.clear()
        conflict_zone = None
        merge_point   = _compute_merge_point(current_roads)
        scenario_reset_counter += 1

        print(f"[bridge] Cenário recebido: {current_scenario_name!r} ({len(current_roads)} estradas, veículos: {current_scenario_vehicles})")
    except Exception:
        pass


def on_mcm(sample):
    global conflict_zone
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

        if label == "MERGE_REQUEST":
            _merge_request_by_mid[manoeuvre_id] = (sender_name, time.time())
            try:
                pos = basic["position"]
                mc_lat, mc_lon = pos["latitude"], pos["longitude"]
                vmc = inner.get("mcmContainer", {}).get("vehicleManoeuvreContainer", {})
                wps = (vmc.get("submaneuvres", [{}])[0]
                           .get("targetRoadResourceIContainer", {})
                           .get("waypoints", []))
                if len(wps) >= 2:
                    d0 = wps[0]["pathPosition"]
                    d1 = wps[1]["pathPosition"]
                    conflict_zone = {
                        "start": {"lat": mc_lat + d0["deltaLatitude"],  "lon": mc_lon + d0["deltaLongitude"]},
                        "end":   {"lat": mc_lat + d1["deltaLatitude"],  "lon": mc_lon + d1["deltaLongitude"]},
                    }
            except Exception:
                pass

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

        if label.startswith("MERGE_GRANT") and to_str == "MC":
            cached = _merge_request_by_mid.get(manoeuvre_id)
            if cached:
                mc_name, ts_cached = cached
                if time.time() - ts_cached <= MERGE_REQUEST_TTL_S:
                    to_str = mc_name

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [MCM] {label:<30}  {sender_name} → {to_str or '?'}  (manoeuvre_id={manoeuvre_id})")

        if label.startswith("MERGE_CONFIRMED") and success:
            vehicle_protocol_states[sender_name] = "MERGING"
        elif label.startswith("EXECUTION_STATUS"):
            vehicle_protocol_states[sender_name] = "NORMAL"
            conflict_zone = None

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
            "t":            round(time.time() - start, 2),
            "scenario":     current_scenario_name,
            "scenario_reset_counter": scenario_reset_counter,
            "roads":        current_roads,
            "vehicles":     [{**v,
                               "state": vehicle_protocol_states.get(v["id"], "NORMAL"),
                               "role": _vehicle_role(v)}
                             for v in vehicle_states.values()
                             if (not current_scenario_vehicles or v["id"] in current_scenario_vehicles)
                             and time.time() - vehicle_last_cam.get(v["id"], 0) <= CAM_TIMEOUT_S],
            "mcm_events":    new_events,
            "conflict_zone": conflict_zone,
            "merge_point":   merge_point if conflict_zone is not None else None,
            "draw_exag":     draw_exag,
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
