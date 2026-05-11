import argparse
import json
import math
import os
import sys
import threading
import time

from cam_builder import build_cam
from mcm_builder import build_merge_request, build_slowdown_request, build_ack


def haversine(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def project_t(lat, lon, road):
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2 = dlat ** 2 + dlon ** 2
    return ((lat - s["lat"]) * dlat + (lon - s["lon"]) * dlon) / L2


def compute_bearing(lat1, lon1, lat2, lon2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def open_zenoh_session(broker):
    import zenoh
    config_json = '{"mode":"client","connect":{"endpoints":["' + broker + '"]}}'
    config = zenoh.Config.from_json5(config_json)
    return zenoh.open(config)


def make_cam_callback(vehicle_id, own_station_id, neighbour_lock, neighbour_states):
    def on_cam(sample):
        try:
            payload = json.loads(bytes(sample.payload).decode())
            station_id = payload.get("stationID") or payload.get("stationId")
            if station_id == own_station_id:
                return
            cam_params = payload["fields"]["cam"]["camParameters"]
            ref = cam_params["basicContainer"]["referencePosition"]
            lat = ref["latitude"]
            lon = ref["longitude"]
            hfc = (cam_params
                   .get("highFrequencyContainer", {})
                   .get("basicVehicleContainerHighFrequency", {}))
            speed_ms = hfc.get("speed", {}).get("speedValue")
            with neighbour_lock:
                neighbour_states[station_id] = {
                    "lat": lat, "lon": lon, "speed_ms": speed_ms, "ts": time.time()
                }
            #print(f"[{vehicle_id}] CAM recebido de stationID={station_id} pos=({lat:.5f}, {lon:.5f})")
        except Exception:
            pass
    return on_cam


MCM_TYPE_NAMES = {1: "request", 2: "response", 9: "acknowledgment"}


def find_vehicle_behind(own_t, own_station_id, road, neighbour_lock, neighbour_states):
    """Return station_id of the vehicle immediately behind on this road, or None."""
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2 = dlat ** 2 + dlon ** 2
    best_id, best_t = None, -1.0
    with neighbour_lock:
        snapshot = dict(neighbour_states)
    for sid, st in snapshot.items():
        t_n = ((st["lat"] - s["lat"]) * dlat + (st["lon"] - s["lon"]) * dlon) / L2
        if not (0.0 <= t_n <= 1.0 and t_n < own_t and t_n > best_t):
            continue
        proj_lat = s["lat"] + t_n * dlat
        proj_lon = s["lon"] + t_n * dlon
        if haversine(proj_lat, proj_lon, st["lat"], st["lon"]) > 25.0:
            continue
        best_t, best_id = t_n, sid
    return best_id


def make_mcm_callback(vehicle_id, own_station_id, session,
                      vehicle_state, road, is_ramp,
                      neighbour_lock, neighbour_states,
                      protocol_lock, protocol_state):

    def _suggested_speed_for(target_id, mc_advice):
        for entry in mc_advice:
            if entry.get("executantID") == target_id:
                try:
                    return entry["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                except (KeyError, IndexError):
                    pass
        with neighbour_lock:
            current = (neighbour_states.get(target_id) or {}).get("speed_ms")
        return current * 0.7 if current else None

    def _send_slowdown(target_id, mc_advice):
        sugg = _suggested_speed_for(target_id, mc_advice)
        if sugg is None:
            sugg = vehicle_state["speed_ms"] * 0.7
        with protocol_lock:
            mid = protocol_state["manoeuvre_id"]
        mcm = build_slowdown_request(
            station_id=own_station_id,
            lat=vehicle_state["lat"],
            lon=vehicle_state["lon"],
            heading=vehicle_state["bearing"],
            speed_ms=vehicle_state["speed_ms"],
            manoeuvre_id=mid,
            next_vehicle_id=target_id,
            suggested_speed_ms=sugg,
        )
        sent_delta = mcm["basicContainer"]["generationDeltaTime"]
        session.put("vanetza/in/mcm", json.dumps(mcm).encode())
        with protocol_lock:
            protocol_state["slowdown_sent"] = True
            protocol_state["slowdown_sent_to"] = target_id
            protocol_state["slowdown_sent_delta_time"] = sent_delta
        print(f"[{vehicle_id}] SLOWDOWN_REQUEST enviado para stationID={target_id} speed={sugg:.2f} m/s")

    def on_mcm(sample):
        try:
            payload = json.loads(bytes(sample.payload).decode())
            sender_id = payload.get("stationID") or payload.get("stationId")
            if sender_id == own_station_id:
                return

            inner = payload["fields"]["payload"]
            basic = inner["basicContainer"]
            mcm_type = basic["mcmType"]
            its_role = basic.get("itssRole", 0)
            delta_time = basic["generationDeltaTime"]
            manoeuvre_id = basic["manoeuvreId"]

            type_name = MCM_TYPE_NAMES.get(mcm_type, mcm_type)
            print(f"[{vehicle_id}] MCM recebido de stationID={sender_id} mcmType={type_name}")

            if is_ramp:
                return

            # ── MERGE_REQUEST (MC → main road vehicles) ──────────────────────
            if mcm_type == 1 and its_role == 1:
                advice = (inner["mcmContainer"]
                          .get("vehicleManoeuvreContainer", {})
                          .get("manoeuvreAdvice", []))
                in_conflict = any(e.get("executantID") == own_station_id for e in advice)
                with protocol_lock:
                    protocol_state["in_conflict"] = in_conflict
                    protocol_state["mc_station_id"] = sender_id
                    protocol_state["manoeuvre_id"] = manoeuvre_id
                    protocol_state["mc_advice"] = advice

                if not in_conflict:
                    return

                own_t = project_t(vehicle_state["lat"], vehicle_state["lon"], road)
                behind_id = find_vehicle_behind(own_t, own_station_id, road,
                                                neighbour_lock, neighbour_states)
                if behind_id is not None:
                    _send_slowdown(behind_id, advice)
                # If no vehicle behind: wait for SLOWDOWN_REQUEST from ahead to trigger ACK chain

            # ── SLOWDOWN_REQUEST (vehicle → vehicle) ──────────────────────────
            elif mcm_type == 1 and its_role == 3:
                vmc = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
                advice = vmc.get("manoeuvreAdvice", [])
                if not advice:
                    return
                if advice[0].get("executantID") != own_station_id:
                    return

                try:
                    sugg_speed = advice[0]["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                except (KeyError, IndexError):
                    sugg_speed = None

                # Use minimum of SLOWDOWN suggestion and MC's own advice for us (if any)
                with protocol_lock:
                    mc_advice = list(protocol_state["mc_advice"])
                own_mc_advice = next((e for e in mc_advice if e.get("executantID") == own_station_id), None)
                if own_mc_advice and sugg_speed is not None:
                    try:
                        mc_speed = own_mc_advice["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                        sugg_speed = min(sugg_speed, mc_speed)
                    except (KeyError, IndexError):
                        pass

                with protocol_lock:
                    protocol_state["slowdown_received"] = True
                    protocol_state["slowdown_sender_id"] = sender_id
                    protocol_state["slowdown_delta_time"] = delta_time
                    protocol_state["manoeuvre_id"] = manoeuvre_id
                    mid = manoeuvre_id

                own_t = project_t(vehicle_state["lat"], vehicle_state["lon"], road)
                behind_id = find_vehicle_behind(own_t, own_station_id, road,
                                                neighbour_lock, neighbour_states)

                if behind_id is not None:
                    _send_slowdown(behind_id, mc_advice)
                else:
                    ack = build_ack(
                        station_id=own_station_id,
                        lat=vehicle_state["lat"],
                        lon=vehicle_state["lon"],
                        manoeuvre_id=mid,
                        acknowledged_delta_time=delta_time,
                    )
                    session.put("vanetza/in/mcm", json.dumps(ack).encode())
                    print(f"[{vehicle_id}] ACK enviado (fim de cadeia, origem stationID={sender_id})")

            # ── ACK (vehicle → vehicle) ────────────────────────────────────────
            elif mcm_type == 9:
                ack_delta = (inner["mcmContainer"]
                             .get("acknowledgmentContainer", {})
                             .get("generationDeltaTime"))
                if ack_delta is None:
                    return

                with protocol_lock:
                    sent = protocol_state["slowdown_sent"]
                    sent_delta = protocol_state["slowdown_sent_delta_time"]
                    received = protocol_state["slowdown_received"]
                    recv_delta = protocol_state["slowdown_delta_time"]
                    mid = protocol_state["manoeuvre_id"]

                if not sent or sent_delta is None:
                    return
                if abs(ack_delta - sent_delta) > 1.0:
                    return

                print(f"[{vehicle_id}] ACK recebido de stationID={sender_id} (cadeia a partir de trás confirmada)")

                if received and recv_delta is not None:
                    ack = build_ack(
                        station_id=own_station_id,
                        lat=vehicle_state["lat"],
                        lon=vehicle_state["lon"],
                        manoeuvre_id=mid,
                        acknowledged_delta_time=recv_delta,
                    )
                    session.put("vanetza/in/mcm", json.dumps(ack).encode())
                    with protocol_lock:
                        sid_up = protocol_state["slowdown_sender_id"]
                    print(f"[{vehicle_id}] ACK propagado para cima (stationID={sid_up})")
                # (10.3: MERGE_GRANT ao MC aqui se in_conflict)

        except Exception as e:
            print(f"[{vehicle_id}] Erro no MCM callback: {e}")

    return on_mcm


def detect_conflicts(vehicle_id, t_mc, L_ramp, speed_mc_ms,
                     merge_lat, merge_lon, main_road, L_main,
                     neighbour_lock, neighbour_states, last_conflict_set):
    eta_s = (1.0 - t_mc) * L_ramp / speed_mc_ms
    if eta_s > CONFLICT_HORIZON_S:
        return last_conflict_set

    with neighbour_lock:
        snapshot = dict(neighbour_states)

    current_conflicts = set()
    s, e = main_road["start"], main_road["end"]
    dlat, dlon = e["lat"] - s["lat"], e["lon"] - s["lon"]
    L2 = dlat ** 2 + dlon ** 2

    for station_id, state in snapshot.items():
        if state["speed_ms"] is None:
            continue
        t_now = ((state["lat"] - s["lat"]) * dlat + (state["lon"] - s["lon"]) * dlon) / L2
        t_pred = min(t_now + state["speed_ms"] * eta_s / L_main, 1.0)
        pred_lat = s["lat"] + t_pred * dlat
        pred_lon = s["lon"] + t_pred * dlon
        if haversine(pred_lat, pred_lon, merge_lat, merge_lon) <= CONFLICT_ZONE_M:
            current_conflicts.add(station_id)

    current_set = frozenset(current_conflicts)
    if current_set != last_conflict_set:
        for sid in sorted(current_set - last_conflict_set):
            print(f"[{vehicle_id}] detetei conflito com veículo stationID={sid} (ETA={eta_s:.1f}s)")
        for sid in sorted(last_conflict_set - current_set):
            print(f"[{vehicle_id}] conflito resolvido com veículo stationID={sid}")
    return current_set


def send_merge_request(session, vehicle_id, own_station_id,
                       lat, lon, bearing, speed_ms,
                       conflict_set, neighbour_lock, neighbour_states,
                       manoeuvre_state):
    RESEND_INTERVAL_S      = 2.0
    SUGGESTED_SPEED_FACTOR = 0.7

    if not conflict_set:
        return

    now = time.time()
    set_changed  = conflict_set != manoeuvre_state["last_conflict_set"]
    time_elapsed = (now - manoeuvre_state["last_send_time"]) >= RESEND_INTERVAL_S

    if not set_changed and not time_elapsed:
        return

    if set_changed:
        manoeuvre_state["manoeuvre_id"]     += 1
        manoeuvre_state["last_conflict_set"] = conflict_set

    manoeuvre_state["last_send_time"] = now

    with neighbour_lock:
        snapshot = dict(neighbour_states)

    conflict_vehicles = [
        (sid, (snapshot.get(sid, {}).get("speed_ms") or speed_ms) * SUGGESTED_SPEED_FACTOR)
        for sid in sorted(conflict_set)
    ]

    mcm = build_merge_request(
        station_id        = own_station_id,
        lat               = lat,
        lon               = lon,
        heading           = bearing,
        speed_ms          = speed_ms,
        manoeuvre_id      = manoeuvre_state["manoeuvre_id"],
        conflict_vehicles = conflict_vehicles,
    )
    session.put("vanetza/in/mcm", json.dumps(mcm).encode())
    print(
        f"[{vehicle_id}] MERGE_REQUEST enviado "
        f"manoeuvre_id={manoeuvre_state['manoeuvre_id']} "
        f"conflitos={sorted(conflict_set)}"
    )


STATION_IDS = {"MC": 10, "A": 11, "B": 12, "C": 13}
CONFLICT_ZONE_M    = 50.0
CONFLICT_HORIZON_S = 2.0


def parse_args():
    parser = argparse.ArgumentParser(description="Vehicle simulation with optional CAM publishing.")
    parser.add_argument("id", nargs="?", default=os.environ.get("VEHICLE_ID"),
                        help="Vehicle ID (MC, A, B, C)")
    parser.add_argument("--broker", default=os.environ.get("ZENOH_BROKER"),
                        help="Zenoh broker endpoint, e.g. tcp/192.168.98.10:7447")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Simulation speed multiplier (e.g. 2 = 2x faster)")
    return parser.parse_args()


def main():
    args = parse_args()
    vehicle_id = args.id
    speed_factor = max(args.speed, 0.1)

    if not vehicle_id:
        print("Erro: especifica o ID do veículo (argumento ou VEHICLE_ID env var)")
        sys.exit(1)

    with open("roads.json") as f:
        roads = {r["id"]: r for r in json.load(f)["roads"]}

    with open("vehicles.json") as f:
        all_vehicles = json.load(f)["vehicles"]

    vehicle = next((v for v in all_vehicles if v["id"] == vehicle_id), None)
    if vehicle is None:
        print(f"veículo '{vehicle_id}' não encontrado em vehicles.json")
        sys.exit(1)

    road = roads[vehicle["road"]]
    L_m = haversine(
        road["start"]["lat"], road["start"]["lon"],
        road["end"]["lat"],   road["end"]["lon"],
    )
    speed_ms = road["speed_limit_kmh"] / 3.6
    bearing = compute_bearing(
        road["start"]["lat"], road["start"]["lon"],
        road["end"]["lat"],   road["end"]["lon"],
    )

    DT = 0.1
    dt_t = speed_ms * DT / L_m
    t = project_t(vehicle["lat"], vehicle["lon"], road)

    is_ramp = road.get("type") == "ramp"
    if is_ramp and road.get("merges_into"):
        main_road = roads[road["merges_into"]]
        merge_lat = road["end"]["lat"]
        merge_lon = road["end"]["lon"]
        L_main = haversine(
            main_road["start"]["lat"], main_road["start"]["lon"],
            main_road["end"]["lat"],   main_road["end"]["lon"],
        )
    else:
        main_road = merge_lat = merge_lon = L_main = None

    neighbour_lock    = threading.Lock()
    neighbour_states  = {}
    last_conflict_set = frozenset()
    manoeuvre_state = {
        "last_conflict_set": frozenset(),
        "manoeuvre_id":      0,
        "last_send_time":    0.0,
    }

    initial_lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
    initial_lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])
    vehicle_state = {
        "lat": initial_lat, "lon": initial_lon,
        "speed_ms": speed_ms, "bearing": bearing,
    }

    protocol_lock = threading.Lock()
    protocol_state = {
        "in_conflict":              False,
        "mc_station_id":            None,
        "manoeuvre_id":             None,
        "mc_advice":                [],
        "slowdown_received":        False,
        "slowdown_sender_id":       None,
        "slowdown_delta_time":      None,
        "slowdown_sent":            False,
        "slowdown_sent_to":         None,
        "slowdown_sent_delta_time": None,
    }

    session = None
    if args.broker:
        try:
            session = open_zenoh_session(args.broker)
            print(f"[{vehicle_id}] Zenoh ligado a {args.broker}")
            own_station_id = STATION_IDS.get(vehicle_id)
            session.declare_subscriber(
                "vanetza/out/cam",
                make_cam_callback(vehicle_id, own_station_id, neighbour_lock, neighbour_states),
            )
            session.declare_subscriber(
                "vanetza/out/mcm",
                make_mcm_callback(vehicle_id, own_station_id, session,
                                  vehicle_state, road, is_ramp,
                                  neighbour_lock, neighbour_states,
                                  protocol_lock, protocol_state),
            )
        except Exception as e:
            print(f"[{vehicle_id}] Aviso: não foi possível ligar ao broker ({e})")
            session = None

    elapsed = 0.0
    while t < 1.0:
        lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
        lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])

        vehicle_state["lat"]      = lat
        vehicle_state["lon"]      = lon
        vehicle_state["speed_ms"] = speed_ms
        vehicle_state["bearing"]  = bearing

        #print(f"[{vehicle_id}] t={elapsed:6.2f}s  lat={lat:.5f}  lon={lon:.5f}  bearing={bearing:.1f}°")

        if session is not None:
            cam = build_cam(lat, lon, bearing, speed_ms, road.get("lane_position"))
            session.put("vanetza/in/cam", json.dumps(cam).encode())

        if is_ramp and session is not None and main_road is not None:
            last_conflict_set = detect_conflicts(
                vehicle_id, t, L_m, speed_ms,
                merge_lat, merge_lon,
                main_road, L_main,
                neighbour_lock, neighbour_states,
                last_conflict_set,
            )
            send_merge_request(
                session, vehicle_id, own_station_id,
                lat, lon, bearing, speed_ms,
                last_conflict_set,
                neighbour_lock, neighbour_states,
                manoeuvre_state,
            )

        t += dt_t
        elapsed += DT
        time.sleep(DT / speed_factor)

    lat = road["end"]["lat"]
    lon = road["end"]["lon"]
    print(f"[{vehicle_id}] t={elapsed:6.2f}s  lat={lat:.5f}  lon={lon:.5f}  [CHEGOU]")

    if session is not None:
        session.close()


if __name__ == "__main__":
    main()
