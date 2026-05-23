import json
import math
import os
import random
import sys
import threading
import time

from cam_builder import build_cam
from mcm_builder import (build_merge_request, build_slowdown_request,
                         build_ack, build_merge_grant, build_slowdown_grant,
                         build_execution_status)


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


def open_zenoh_session_with_retry(url, retries=15, delay=2.0):
    for attempt in range(retries):
        try:
            return open_zenoh_session(url)
        except Exception as e:
            print(f"Zenoh connect to {url} falhou ({e}), retry {attempt + 1}/{retries}...")
            time.sleep(delay)
    raise RuntimeError(f"Não foi possível ligar a {url} após {retries} tentativas")


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


def find_vehicle_behind(own_t, own_station_id, road, neighbour_lock, neighbour_states,
                        exclude_ids=None):
    """Return station_id of the vehicle immediately behind on this road, or None."""
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2 = dlat ** 2 + dlon ** 2
    best_id, best_t = None, -1.0
    with neighbour_lock:
        snapshot = dict(neighbour_states)
    for sid, st in snapshot.items():
        if exclude_ids and sid in exclude_ids:
            continue
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
                      protocol_lock, protocol_state,
                      demo_mode=False, demo_step_delay=0.0):

    def _demo_pause():
        if not demo_mode:
            return
        with protocol_lock:
            active = protocol_state.get("demo_active", False)
        if active:
            time.sleep(demo_step_delay)

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

    def _send_ack(delta_time, mid):
        _demo_pause()
        ack = build_ack(
            station_id=own_station_id,
            lat=vehicle_state["lat"],
            lon=vehicle_state["lon"],
            manoeuvre_id=mid,
            acknowledged_delta_time=delta_time,
        )
        session.put("vanetza/in/mcm", json.dumps(ack).encode())

    def _send_slowdown(target_id, mc_advice):
        _demo_pause()
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
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] SLOWDOWN_REQUEST enviado para stationID={target_id} speed={sugg:.2f} m/s")

    def _send_slowdown_grant(to_id):
        _demo_pause()
        grant_id = random.randint(128, 255)
        grant = build_slowdown_grant(
            station_id=own_station_id,
            lat=vehicle_state["lat"],
            lon=vehicle_state["lon"],
            manoeuvre_id=grant_id,
        )
        sent_delta = grant["basicContainer"]["generationDeltaTime"]
        session.put("vanetza/in/mcm", json.dumps(grant).encode())
        with protocol_lock:
            protocol_state["slowdown_grant_sent"] = True
            protocol_state["slowdown_grant_sent_delta"] = sent_delta
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] SLOWDOWN_GRANT enviado para stationID={to_id} (grant_id={grant_id})")

    def _send_merge_grant():
        _demo_pause()
        with protocol_lock:
            if protocol_state["merge_grant_sent"]:
                return
            if not protocol_state["in_conflict"]:
                return
            mc_id = protocol_state["mc_station_id"]
            mid   = protocol_state["manoeuvre_id"]
        if mc_id is None:
            return
        grant = build_merge_grant(
            station_id=own_station_id,
            lat=vehicle_state["lat"],
            lon=vehicle_state["lon"],
            manoeuvre_id=mid,
        )
        sent_delta = grant["basicContainer"]["generationDeltaTime"]
        session.put("vanetza/in/mcm", json.dumps(grant).encode())
        with protocol_lock:
            protocol_state["merge_grant_sent"] = True
            protocol_state["merge_grant_sent_delta"] = sent_delta
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] MERGE_GRANT enviado ao MC stationID={mc_id}")

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
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{vehicle_id}] MCM recebido de stationID={sender_id} mcmType={type_name}")

            # ── mcmType=2, itssRole=3: SLOWDOWN_GRANT (128-255) ou MERGE_GRANT (0-127) ──
            if mcm_type == 2 and its_role == 3:
                if manoeuvre_id >= 128:
                    # ── SLOWDOWN_GRANT (veículo atrás → este veículo) ──────────
                    if is_ramp:
                        return  # MC não recebe SLOWDOWN_GRANT
                    with protocol_lock:
                        sent_to = protocol_state["slowdown_sent_to"]
                    if sender_id != sent_to:
                        return  # não é de quem esperávamos
                    _send_ack(delta_time, manoeuvre_id)
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] [{vehicle_id}] SLOWDOWN_GRANT recebido de stationID={sender_id} (grant_id={manoeuvre_id})")
                    with protocol_lock:
                        protocol_state["slowdown_grant_received"] = True
                        protocol_state["slowdown_grant_sender_id"] = sender_id
                        received = protocol_state["slowdown_received"]
                        sender_ahead = protocol_state["slowdown_sender_id"]
                    # Cada veículo decide independentemente (por agora: aceita sempre)
                    if received and sender_ahead is not None:
                        _send_slowdown_grant(sender_ahead)
                    else:
                        _send_merge_grant()  # veículo A: sem SLOWDOWN_REQUEST recebido → grant ao MC
                else:
                    # ── MERGE_GRANT (veículo da estrada → MC) ─────────────────
                    if not is_ramp:
                        return
                    _send_ack(delta_time, manoeuvre_id)
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] [{vehicle_id}] MERGE_GRANT recebido de stationID={sender_id} manoeuvre_id={manoeuvre_id}")
                    with protocol_lock:
                        protocol_state["grants_received"].add(sender_id)
                        if protocol_state["grant_timeout"] is None:
                            effective_timeout = GRANT_TIMEOUT_S + (demo_step_delay * 10 if demo_mode else 0.0)
                            protocol_state["grant_timeout"] = time.time() + effective_timeout
                return

            # ── mcmType=2, itssRole=1: execution_status do MC ────────────────
            if mcm_type == 2 and its_role == 1:
                response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                success = (response == 0)
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{vehicle_id}] execution_status recebido de MC={sender_id} success={success}")
                with protocol_lock:
                    protocol_state["in_conflict"] = False
                    if demo_mode and success:
                        protocol_state["demo_active"] = False
                return

            if is_ramp:
                return

            # ── MERGE_REQUEST (MC → veículos da estrada) ──────────────────────
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
                    if demo_mode:
                        protocol_state["demo_active"] = True

                if not in_conflict:
                    return

                _send_ack(delta_time, manoeuvre_id)

                own_t = project_t(vehicle_state["lat"], vehicle_state["lon"], road)
                behind_id = find_vehicle_behind(own_t, own_station_id, road,
                                                neighbour_lock, neighbour_states,
                                                exclude_ids={sender_id})
                if behind_id is not None:
                    _send_slowdown(behind_id, advice)
                elif len({e.get("executantID") for e in advice}) == 1:
                    # Sole conflict vehicle — no SLOWDOWN chain needed; grant directly.
                    _send_merge_grant()
                # else: other conflict vehicles are ahead; wait for their SLOWDOWN_REQUEST

            # ── SLOWDOWN_REQUEST (veículo → veículo) ──────────────────────────
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

                _send_ack(delta_time, mid)

                own_t = project_t(vehicle_state["lat"], vehicle_state["lon"], road)
                with protocol_lock:
                    mc_id = protocol_state["mc_station_id"]
                behind_id = find_vehicle_behind(own_t, own_station_id, road,
                                                neighbour_lock, neighbour_states,
                                                exclude_ids={mc_id} if mc_id is not None else None)

                if behind_id is not None:
                    _send_slowdown(behind_id, mc_advice)
                else:
                    # Fim de cadeia: decide e envia SLOWDOWN_GRANT (não ACK)
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] [{vehicle_id}] Fim de cadeia — a calcular e enviar SLOWDOWN_GRANT para stationID={sender_id}")
                    _send_slowdown_grant(sender_id)

            # ── ACK (confirmação de entrega) ───────────────────────────────────
            elif mcm_type == 9:
                ack_delta = (inner["mcmContainer"]
                             .get("acknowledgmentContainer", {})
                             .get("generationDeltaTime"))
                if ack_delta is None:
                    return

                with protocol_lock:
                    slowdown_sent_delta   = protocol_state["slowdown_sent_delta_time"]
                    slowdown_grant_delta  = protocol_state.get("slowdown_grant_sent_delta")
                    merge_grant_delta     = protocol_state.get("merge_grant_sent_delta")

                def _matches(ref):
                    return ref is not None and abs(ack_delta - ref) <= 1.0

                if _matches(slowdown_sent_delta):
                    label = "SLOWDOWN_REQUEST"
                elif _matches(slowdown_grant_delta):
                    label = "SLOWDOWN_GRANT"
                elif _matches(merge_grant_delta):
                    label = "MERGE_GRANT"
                else:
                    label = "mensagem desconhecida"

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{vehicle_id}] ACK de entrega recebido de stationID={sender_id} (confirma {label})")

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
                       manoeuvre_state, demo_step_delay=0.0):
    RESEND_INTERVAL_S      = 2.0 + 120 
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
    ts = time.strftime("%H:%M:%S")
    print(
        f"[{ts}] [{vehicle_id}] MERGE_REQUEST enviado "
        f"manoeuvre_id={manoeuvre_state['manoeuvre_id']} "
        f"conflitos={sorted(conflict_set)}"
    )


CONFLICT_ZONE_M    = 50.0
CONFLICT_HORIZON_S = 2.0
GRANT_TIMEOUT_S    = 5.0


def run_scenario(scenario, vehicle_id, own_station_id, vanetza_session):
    roads = {r["id"]: r for r in scenario["roads"]}

    vehicle_cfg = next(
        (v for v in scenario["vehicles"] if v["station_id"] == own_station_id),
        None,
    )
    if vehicle_cfg is None:
        print(f"[{vehicle_id}] Aviso: station_id={own_station_id} não encontrado no cenário — a saltar")
        return

    speed_factor    = max(float(scenario.get("speed_multiplier", 1.0)), 0.1)
    demo_mode       = bool(scenario.get("demo_mode", False))
    demo_step_delay = float(scenario.get("demo_step_delay_s", 0.0)) if demo_mode else 0.0

    road = roads[vehicle_cfg["road"]]
    L_m = haversine(
        road["start"]["lat"], road["start"]["lon"],
        road["end"]["lat"],   road["end"]["lon"],
    )
    #speed_ms = road["speed_limit_kmh"] / 3.6

    speed_kmh = vehicle_cfg.get("speed_kmh", road["speed_limit_kmh"])
    speed_ms = speed_kmh / 3.6
    
    bearing = compute_bearing(
        road["start"]["lat"], road["start"]["lon"],
        road["end"]["lat"],   road["end"]["lon"],
    )

    DT   = 0.1
    dt_t = speed_ms * DT / L_m
    t    = project_t(vehicle_cfg["lat"], vehicle_cfg["lon"], road)

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
    manoeuvre_state   = {
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

    protocol_lock  = threading.Lock()
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
        "slowdown_grant_received":  False,
        "slowdown_grant_sender_id": None,
        "slowdown_grant_sent":      False,
        "slowdown_grant_sent_delta": None,
        "merge_grant_sent":         False,
        "merge_grant_sent_delta":   None,
        "grants_received":          set(),
        "grant_timeout":            None,
        "merge_decided":            False,
        "demo_active":              False,
    }

    print(f"[{vehicle_id}] Cenário iniciado: road={vehicle_cfg['road']} "
          f"pos=({vehicle_cfg['lat']:.5f}, {vehicle_cfg['lon']:.5f}) "
          f"speed={speed_ms:.1f}m/s speed_factor={speed_factor}")

    cam_sub = vanetza_session.declare_subscriber(
        "vanetza/out/cam",
        make_cam_callback(vehicle_id, own_station_id, neighbour_lock, neighbour_states),
    )
    mcm_sub = vanetza_session.declare_subscriber(
        "vanetza/out/mcm",
        make_mcm_callback(vehicle_id, own_station_id, vanetza_session,
                          vehicle_state, road, is_ramp,
                          neighbour_lock, neighbour_states,
                          protocol_lock, protocol_state,
                          demo_mode, demo_step_delay),
    )
    

    length_m = vehicle_cfg.get("length_m", 4.5)
    width_m = vehicle_cfg.get("width_m", 1.8)
    try:
        elapsed = 0.0
        while t < 1.0:
            lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
            lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])

            vehicle_state["lat"]      = lat
            vehicle_state["lon"]      = lon
            vehicle_state["speed_ms"] = speed_ms
            vehicle_state["bearing"]  = bearing


            cam = build_cam(lat, lon, bearing, speed_ms, road.get("lane_position"),length_m, width_m,)
            vanetza_session.put("vanetza/in/cam", json.dumps(cam).encode())

            should_advance = True

            if is_ramp and main_road is not None:
                last_conflict_set = detect_conflicts(
                    vehicle_id, t, L_m, speed_ms,
                    merge_lat, merge_lon,
                    main_road, L_main,
                    neighbour_lock, neighbour_states,
                    last_conflict_set,
                )

                with protocol_lock:
                    decided = protocol_state["merge_decided"]

                if not decided:
                    send_merge_request(
                        vanetza_session, vehicle_id, own_station_id,
                        lat, lon, bearing, speed_ms,
                        last_conflict_set,
                        neighbour_lock, neighbour_states,
                        manoeuvre_state,
                        demo_step_delay,
                    )
                    if demo_mode and last_conflict_set:
                        with protocol_lock:
                            if not protocol_state["demo_active"]:
                                protocol_state["demo_active"] = True

                if last_conflict_set:
                    if not decided:
                        with protocol_lock:
                            grants  = frozenset(protocol_state["grants_received"])
                            timeout = protocol_state["grant_timeout"]

                    if not decided:
                        all_granted = last_conflict_set.issubset(grants)
                        timed_out   = timeout is not None and time.time() > timeout
                        approaching = (1.0 - t) * L_m <= CONFLICT_ZONE_M

                        if all_granted:
                            now = time.time()
                            with neighbour_lock:
                                snap = dict(neighbour_states)
                            cam_freshness_s = 2.0 + (demo_step_delay * 3 if demo_mode else 0.0)
                            valid = all(
                                sid in snap and now - snap[sid]["ts"] < cam_freshness_s
                                for sid in last_conflict_set
                            )
                            ts = time.strftime("%H:%M:%S")
                            if valid:
                                print(f"[{ts}] [{vehicle_id}] MERGE_GRANT validado — a executar merge grants={sorted(grants)}")
                                status = build_execution_status(
                                    own_station_id, lat, lon,
                                    manoeuvre_state["manoeuvre_id"], success=True,
                                )
                                vanetza_session.put("vanetza/in/mcm", json.dumps(status).encode())
                                with protocol_lock:
                                    protocol_state["merge_decided"] = True
                                    if demo_mode:
                                        protocol_state["demo_active"] = False
                            else:
                                print(f"[{ts}] [{vehicle_id}] MERGE_GRANT inválido (CAMs stale) — a renegociar")
                                with protocol_lock:
                                    protocol_state["grants_received"] = set()
                                    protocol_state["grant_timeout"]   = None
                        elif timed_out:
                            ts = time.strftime("%H:%M:%S")
                            print(f"[{ts}] [{vehicle_id}] MERGE_GRANT timeout — fallback "
                                  f"(grants={sorted(grants)}, esperados={sorted(last_conflict_set)})")
                            status = build_execution_status(
                                own_station_id, lat, lon,
                                manoeuvre_state["manoeuvre_id"], success=False,
                            )
                            vanetza_session.put("vanetza/in/mcm", json.dumps(status).encode())
                            with protocol_lock:
                                protocol_state["grants_received"] = set()
                                protocol_state["grant_timeout"]   = None
                            manoeuvre_state["manoeuvre_id"] += 1
                        elif approaching:
                            ts = time.strftime("%H:%M:%S")
                            print(f"[{ts}] [{vehicle_id}] a aguardar MERGE_GRANT... "
                                  f"grants={sorted(grants)} / esperados={sorted(last_conflict_set)}")
                            should_advance = False

            with protocol_lock:
                demo_active = protocol_state.get("demo_active", False)
            if demo_active:
                should_advance = False

            if should_advance:
                t += dt_t
            elapsed += DT
            time.sleep(DT / speed_factor + (demo_step_delay if demo_active else 0.0))

    finally:
        cam_sub.undeclare()
        mcm_sub.undeclare()

    lat = road["end"]["lat"]
    lon = road["end"]["lon"]
    if is_ramp:
        with protocol_lock:
            decided = protocol_state["merge_decided"]
        ts = time.strftime("%H:%M:%S")
        if decided:
            print(f"[{ts}] [{vehicle_id}] MERGE EXECUTADO COM SUCESSO")
        else:
            print(f"[{ts}] [{vehicle_id}] MERGE: chegou ao fim da rampa sem grants suficientes")
    print(f"[{vehicle_id}] t={elapsed:6.2f}s  lat={lat:.5f}  lon={lon:.5f}  [CHEGOU]")


def main():
    vehicle_id     = os.environ.get("VEHICLE_ID")
    own_station_id = os.environ.get("STATION_ID")
    vanetza_url    = os.environ.get("VANETZA_ZENOH_URL")
    coord_url      = os.environ.get("COORDINATOR_ZENOH_URL")

    if not vehicle_id or own_station_id is None:
        print("Erro: VEHICLE_ID e STATION_ID são obrigatórios")
        sys.exit(1)

    own_station_id = int(own_station_id)

    print(f"[{vehicle_id}] A ligar ao Vanetza ({vanetza_url})...")
    vanetza_session = open_zenoh_session_with_retry(vanetza_url)
    print(f"[{vehicle_id}] Vanetza ligado.")

    print(f"[{vehicle_id}] A ligar ao coordenador ({coord_url})...")
    coord_session = open_zenoh_session(coord_url)
    print(f"[{vehicle_id}] Coordenador ligado.")

    scenario_event = threading.Event()
    pending        = {}
    pending_skip   = [False]

    def on_scenario(sample):
        try:
            data   = json.loads(bytes(sample.payload).decode())
            active = data.get("active_vehicles", [])
            if own_station_id not in active:
                print(f"[{vehicle_id}] Cenário recebido mas station_id={own_station_id} "
                      f"não está em active_vehicles — a saltar")
                pending_skip[0] = True
            else:
                pending.clear()
                pending.update(data)
                pending_skip[0] = False
            scenario_event.set()
        except Exception as e:
            print(f"[{vehicle_id}] Erro ao processar cenário: {e}")

    coord_session.declare_subscriber("coordinator/scenario", on_scenario)
    print(f"[{vehicle_id}] Pronto. A aguardar cenário do coordenador...")

    def ready_loop():
        ready_payload = json.dumps({"station_id": own_station_id}).encode()
        while not scenario_event.is_set():
            coord_session.put(f"coordinator/ready/{own_station_id}", ready_payload)
            time.sleep(2.0)

    while True:
        ready_thread = threading.Thread(target=ready_loop, daemon=True)
        ready_thread.start()
        scenario_event.wait()
        scenario_event.clear()

        if pending_skip[0]:
            pending_skip[0] = False
            coord_session.put(
                f"coordinator/done/{own_station_id}",
                json.dumps({"station_id": own_station_id, "skipped": True}).encode(),
            )
            continue

        scenario = dict(pending)
        print(f"[{vehicle_id}] Cenário recebido: {scenario.get('name', '?')}")
        try:
            run_scenario(scenario, vehicle_id, own_station_id, vanetza_session)
        except Exception as e:
            print(f"[{vehicle_id}] Erro no cenário: {e}")

        coord_session.put(
            f"coordinator/done/{own_station_id}",
            json.dumps({"station_id": own_station_id, "vehicle_id": vehicle_id}).encode(),
        )
        print(f"[{vehicle_id}] Done publicado — a aguardar próximo cenário")


if __name__ == "__main__":
    main()
