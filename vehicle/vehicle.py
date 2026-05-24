import json
import math
import os
import random
import sys
import threading
import time

from cam_builder import build_cam
from mcm_builder import (build_merge_request, build_slowdown_request,
                         build_merge_grant, build_slowdown_grant,
                         build_merge_confirmed, build_execution_status)


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
                      merge_lat=None, merge_lon=None, main_road_ref=None, L_main=None,
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

    def _calculate_target_speed(mc_eta_s, lat, lon):
        """Speed to reach the conflict zone entry just as MC arrives."""
        if main_road_ref is None or L_main is None or merge_lat is None:
            return None
        if mc_eta_s is None or mc_eta_s <= 0:
            return None
        t_v = project_t(lat, lon, main_road_ref)
        t_m = project_t(merge_lat, merge_lon, main_road_ref)
        dist_safe = (t_m - t_v) * L_main - CONFLICT_ZONE_M
        return max(0.0, dist_safe / mc_eta_s) if dist_safe > 0 else 0.0

    def _send_slowdown(target_id, mc_advice):
        _demo_pause()
        with protocol_lock:
            mc_eta_s_local = protocol_state.get("mc_eta_s")
        with neighbour_lock:
            t_state = dict(neighbour_states).get(target_id, {})
        if mc_eta_s_local and t_state.get("lat") is not None:
            sugg = _calculate_target_speed(mc_eta_s_local, t_state["lat"], t_state["lon"])
        else:
            sugg = _suggested_speed_for(target_id, mc_advice)
        if sugg is None:
            sugg = vehicle_state["speed_ms"] * 0.7
        # Vehicle behind must not exceed own target (avoid rear collision)
        with protocol_lock:
            own_target = protocol_state.get("own_target_speed_ms")
        if own_target is not None:
            sugg = min(sugg, own_target)
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
        session.put("vanetza/in/mcm", json.dumps(mcm).encode())
        with protocol_lock:
            protocol_state["slowdown_sent"] = True
            protocol_state["slowdown_sent_to"] = target_id
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] SLOWDOWN_REQUEST enviado para stationID={target_id} speed={sugg * 3.6:.1f} km/h")

    def _send_slowdown_grant(to_id, success=True):
        _demo_pause()
        grant_id = random.randint(128, 255)
        grant = build_slowdown_grant(
            station_id=own_station_id,
            lat=vehicle_state["lat"],
            lon=vehicle_state["lon"],
            manoeuvre_id=grant_id,
            success=success,
        )
        session.put("vanetza/in/mcm", json.dumps(grant).encode())
        with protocol_lock:
            protocol_state["slowdown_grant_sent"] = True
        ts = time.strftime("%H:%M:%S")
        label = "SLOWDOWN_GRANT" if success else "SLOWDOWN_GRANT(recusa)"
        print(f"[{ts}] [{vehicle_id}] {label} enviado para stationID={to_id} (grant_id={grant_id})")

    def _available_dist():
        if main_road_ref is None or L_main is None or merge_lat is None:
            return None
        t_v = project_t(vehicle_state["lat"], vehicle_state["lon"], main_road_ref)
        t_m = project_t(merge_lat, merge_lon, main_road_ref)
        return max(0.0, (t_m - t_v) * L_main - CONFLICT_ZONE_M)

    def _send_merge_grant(success=True):
        _demo_pause()
        with protocol_lock:
            if protocol_state["merge_grant_sent"]:
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
            success=success,
        )
        session.put("vanetza/in/mcm", json.dumps(grant).encode())
        with protocol_lock:
            protocol_state["merge_grant_sent"] = True
        ts = time.strftime("%H:%M:%S")
        label = "MERGE_GRANT" if success else "MERGE_GRANT(recusa)"
        print(f"[{ts}] [{vehicle_id}] {label} enviado ao MC stationID={mc_id}")

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
                    # ── SLOWDOWN_GRANT ou SLOWDOWN_REFUSE (veículo atrás → este) ──
                    if is_ramp:
                        return  # MC não recebe estas mensagens
                    with protocol_lock:
                        sent_to = protocol_state["slowdown_sent_to"]
                    if sender_id != sent_to:
                        return  # não é de quem esperávamos
                    ts = time.strftime("%H:%M:%S")
                    response_code = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    if response_code != 0:
                        # ── SLOWDOWN_REFUSE: propaga recusa para a frente ──────
                        print(f"[{ts}] [{vehicle_id}] SLOWDOWN_REFUSE recebido de stationID={sender_id}")
                        with protocol_lock:
                            received     = protocol_state["slowdown_received"]
                            sender_ahead = protocol_state["slowdown_sender_id"]
                        if received and sender_ahead is not None:
                            _send_slowdown_grant(sender_ahead, success=False)
                        else:
                            _send_merge_grant(success=False)
                        return
                    # ── SLOWDOWN_GRANT ────────────────────────────────────────
                    print(f"[{ts}] [{vehicle_id}] SLOWDOWN_GRANT recebido de stationID={sender_id} (grant_id={manoeuvre_id})")
                    with protocol_lock:
                        protocol_state["slowdown_grant_received"] = True
                        protocol_state["slowdown_grant_sender_id"] = sender_id
                        received     = protocol_state["slowdown_received"]
                        sender_ahead = protocol_state["slowdown_sender_id"]
                        mc_advice    = list(protocol_state["mc_advice"])
                        apply_speed  = protocol_state.get("own_target_speed_ms")
                    if apply_speed is None:
                        apply_speed = _suggested_speed_for(own_station_id, mc_advice)
                        if apply_speed is None:
                            apply_speed = vehicle_state["speed_ms"] * 0.7
                    avail = _available_dist()
                    ts = time.strftime("%H:%M:%S")
                    if avail is not None:
                        ok, brake_d = can_brake_in_time(vehicle_state["speed_ms"], apply_speed, avail)
                        if ok:
                            print(f"[{ts}] [{vehicle_id}] aceita: dist_travagem={brake_d:.1f}m, disponivel={avail:.1f}m")
                            vehicle_state["target_speed_ms"] = apply_speed
                            with protocol_lock:
                                protocol_state["slowed_down"] = True
                            print(f"[{ts}] [{vehicle_id}] velocidade alvo aplicada: {apply_speed * 3.6:.1f} km/h (grant recebido)")
                            if received and sender_ahead is not None:
                                _send_slowdown_grant(sender_ahead)
                            else:
                                _send_merge_grant()
                        else:
                            print(f"[{ts}] [{vehicle_id}] recusa: dist_travagem={brake_d:.1f}m > disponivel={avail:.1f}m")
                            if received and sender_ahead is not None:
                                _send_slowdown_grant(sender_ahead, success=False)
                            else:
                                _send_merge_grant(success=False)
                    else:
                        # sem informação de distância: aceita (compatibilidade)
                        vehicle_state["target_speed_ms"] = apply_speed
                        with protocol_lock:
                            protocol_state["slowed_down"] = True
                        print(f"[{ts}] [{vehicle_id}] velocidade alvo aplicada: {apply_speed * 3.6:.1f} km/h (grant recebido)")
                        if received and sender_ahead is not None:
                            _send_slowdown_grant(sender_ahead)
                        else:
                            _send_merge_grant()
                else:
                    # ── MERGE_GRANT (veículo da estrada → MC) ─────────────────
                    if not is_ramp:
                        return
                    ts = time.strftime("%H:%M:%S")
                    response_code = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    if response_code != 0:
                        print(f"[{ts}] [{vehicle_id}] MERGE_GRANT(recusa) de stationID={sender_id} — a reiniciar negociação")
                        with protocol_lock:
                            protocol_state["grants_received"] = set()
                            protocol_state["grant_timeout"] = None
                            protocol_state["merge_grant_sent"] = False
                        return
                    print(f"[{ts}] [{vehicle_id}] MERGE_GRANT recebido de stationID={sender_id} manoeuvre_id={manoeuvre_id}")
                    with protocol_lock:
                        protocol_state["grants_received"].add(sender_id)
                        if protocol_state["grant_timeout"] is None:
                            effective_timeout = GRANT_TIMEOUT_S + (demo_step_delay * 10 if demo_mode else 0.0)
                            protocol_state["grant_timeout"] = time.time() + effective_timeout
                return

            # ── mcmType=2, itssRole=1: merge_confirmed ou execution_status do MC
            if mcm_type == 2 and its_role == 1:
                response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                ts = time.strftime("%H:%M:%S")

                if response == 2:
                    # EXECUTION_STATUS: MC trocou de faixa → retomar velocidade
                    print(f"[{ts}] [{vehicle_id}] execution_status de MC={sender_id} — a retomar velocidade")
                    with protocol_lock:
                        slowed = protocol_state.get("slowed_down", False)
                        if slowed:
                            road_spd = vehicle_state.get("road_speed_ms", vehicle_state["speed_ms"])
                            vehicle_state["target_speed_ms"] = road_spd
                            protocol_state["slowed_down"] = False
                            print(f"[{ts}] [{vehicle_id}] velocidade retomada ({road_spd * 3.6:.1f} km/h)")

                elif response == 0:
                    # MERGE_CONFIRMED: acordo alcançado → terminar demo, conflito resolvido
                    print(f"[{ts}] [{vehicle_id}] merge_confirmed de MC={sender_id} — acordo alcançado")
                    with protocol_lock:
                        protocol_state["in_conflict"] = False
                        if demo_mode:
                            protocol_state["demo_active"] = False

                elif response == 1:
                    # MERGE_CONFIRMED(ABORT): timeout/fallback
                    print(f"[{ts}] [{vehicle_id}] merge_confirmed(abort) de MC={sender_id}")

                return

            if is_ramp:
                return

            # ── MERGE_REQUEST (MC → veículos da estrada) ──────────────────────
            if mcm_type == 1 and its_role == 1:
                advice = (inner["mcmContainer"]
                          .get("vehicleManoeuvreContainer", {})
                          .get("manoeuvreAdvice", []))

                # Independent conflict check: predict own position at MC's ETA
                submaneuvres = (inner["mcmContainer"]
                                .get("vehicleManoeuvreContainer", {})
                                .get("submaneuvres", []))
                temporal     = submaneuvres[0].get("temporalCharateristics", {}) if submaneuvres else {}
                t_start_ms   = temporal.get("tRROccupancyStartTime", 2000)
                t_end_ms     = temporal.get("tRROccupancyEndTime",   5000)
                mc_eta_s     = (t_start_ms + t_end_ms) / 2 / 1000.0

                in_conflict = False
                if merge_lat is not None and main_road_ref is not None and L_main:
                    s, e     = main_road_ref["start"], main_road_ref["end"]
                    dlat     = e["lat"] - s["lat"]
                    dlon     = e["lon"] - s["lon"]
                    own_speed = vehicle_state["speed_ms"]
                    own_t_now = project_t(vehicle_state["lat"], vehicle_state["lon"], main_road_ref)
                    t_pred    = min(own_t_now + own_speed * mc_eta_s / L_main, 1.0)
                    pred_lat  = s["lat"] + t_pred * dlat
                    pred_lon  = s["lon"] + t_pred * dlon
                    if haversine(pred_lat, pred_lon, merge_lat, merge_lon) <= CONFLICT_ZONE_M:
                        in_conflict = True

                own_target = _calculate_target_speed(mc_eta_s, vehicle_state["lat"], vehicle_state["lon"])

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{vehicle_id}] MERGE_REQUEST de stationID={sender_id} "
                      f"eta={mc_eta_s:.1f}s in_conflict={in_conflict}")
                if own_target is not None and in_conflict:
                    print(f"[{ts}] [{vehicle_id}] velocidade alvo calculada: {own_target * 3.6:.1f} km/h (ETA={mc_eta_s:.1f}s)")

                with protocol_lock:
                    protocol_state["in_conflict"] = in_conflict
                    protocol_state["mc_station_id"] = sender_id
                    protocol_state["manoeuvre_id"] = manoeuvre_id
                    protocol_state["mc_advice"] = advice
                    protocol_state["mc_eta_s"] = mc_eta_s
                    protocol_state["own_target_speed_ms"] = own_target
                    if demo_mode:
                        protocol_state["demo_active"] = True

                if not in_conflict:
                    _send_merge_grant()
                    return

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
                    # Cap own target at the speed of the vehicle ahead (avoids rear collision)
                    if sugg_speed is not None:
                        prev = protocol_state.get("own_target_speed_ms")
                        if prev is not None:
                            protocol_state["own_target_speed_ms"] = min(prev, sugg_speed)
                        else:
                            protocol_state["own_target_speed_ms"] = sugg_speed

                own_t = project_t(vehicle_state["lat"], vehicle_state["lon"], road)
                with protocol_lock:
                    mc_id = protocol_state["mc_station_id"]
                behind_id = find_vehicle_behind(own_t, own_station_id, road,
                                                neighbour_lock, neighbour_states,
                                                exclude_ids={mc_id} if mc_id is not None else None)

                if behind_id is not None:
                    _send_slowdown(behind_id, mc_advice)
                else:
                    # Fim de cadeia: verifica se consegue abrandar; envia GRANT ou REFUSE
                    with protocol_lock:
                        apply_speed = protocol_state.get("own_target_speed_ms")
                    if apply_speed is None:
                        apply_speed = sugg_speed if sugg_speed is not None else vehicle_state["speed_ms"] * 0.7
                    avail = _available_dist()
                    ts = time.strftime("%H:%M:%S")
                    if avail is not None:
                        ok, brake_d = can_brake_in_time(vehicle_state["speed_ms"], apply_speed, avail)
                        if ok:
                            print(f"[{ts}] [{vehicle_id}] aceita: dist_travagem={brake_d:.1f}m, disponivel={avail:.1f}m")
                            vehicle_state["target_speed_ms"] = apply_speed
                            with protocol_lock:
                                protocol_state["slowed_down"] = True
                            print(f"[{ts}] [{vehicle_id}] velocidade alvo aplicada: {apply_speed * 3.6:.1f} km/h (fim de cadeia)")
                            print(f"[{ts}] [{vehicle_id}] Fim de cadeia — a enviar SLOWDOWN_GRANT para stationID={sender_id}")
                            _send_slowdown_grant(sender_id)
                        else:
                            print(f"[{ts}] [{vehicle_id}] recusa: dist_travagem={brake_d:.1f}m > disponivel={avail:.1f}m")
                            _send_slowdown_grant(sender_id, success=False)
                    else:
                        # sem informação de distância: aceita (compatibilidade)
                        vehicle_state["target_speed_ms"] = apply_speed
                        with protocol_lock:
                            protocol_state["slowed_down"] = True
                        print("[{ts}] [{vehicle_id}] aceita: sem informação de distância, a aplicar velocidade sugerida")
                        print(f"[{ts}] [{vehicle_id}] velocidade alvo aplicada: {apply_speed * 3.6:.1f} km/h (fim de cadeia)")
                        print(f"[{ts}] [{vehicle_id}] Fim de cadeia — a enviar SLOWDOWN_GRANT para stationID={sender_id}")
                        _send_slowdown_grant(sender_id)

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
                       t_mc, L_ramp,
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

    eta_s        = (1.0 - t_mc) * L_ramp / speed_ms
    eta_start_ms = max(0, int((eta_s - 2.0) * 1000))
    eta_end_ms   = int((eta_s + 2.0) * 1000)

    mcm = build_merge_request(
        station_id        = own_station_id,
        lat               = lat,
        lon               = lon,
        heading           = bearing,
        speed_ms          = speed_ms,
        manoeuvre_id      = manoeuvre_state["manoeuvre_id"],
        conflict_vehicles = conflict_vehicles,
        eta_start_ms      = eta_start_ms,
        eta_end_ms        = eta_end_ms,
    )
    session.put("vanetza/in/mcm", json.dumps(mcm).encode())
    ts = time.strftime("%H:%M:%S")
    print(
        f"[{ts}] [{vehicle_id}] MERGE_REQUEST enviado "
        f"manoeuvre_id={manoeuvre_state['manoeuvre_id']} "
        f"conflitos={sorted(conflict_set)} eta={eta_s:.1f}s"
    )


CONFLICT_ZONE_M    = 50.0
CONFLICT_HORIZON_S = 4.0
GRANT_TIMEOUT_S    = 5.0
DECELERATION_MS2   = 7   # m/s² — travagem confortável
ACCELERATION_MS2   = 2   # m/s² — aceleração confortável de retoma


def can_brake_in_time(cur_speed_ms, target_speed_ms, avail_dist_m):
    """Returns (can_brake: bool, braking_dist_m: float)."""
    if cur_speed_ms <= target_speed_ms:
        return True, 0.0
    d = (cur_speed_ms ** 2 - target_speed_ms ** 2) / (2 * DECELERATION_MS2)
    return d <= avail_dist_m, d


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
    speed_ms = road["speed_limit_kmh"] / 3.6
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
        ramp_road = next(
            (r for r in scenario["roads"]
             if r.get("type") == "ramp" and r.get("merges_into") == road["id"]),
            None,
        )
        if ramp_road:
            main_road = road
            merge_lat = ramp_road["end"]["lat"]
            merge_lon = ramp_road["end"]["lon"]
            L_main    = L_m
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
        "target_speed_ms": speed_ms,
        "road_speed_ms": speed_ms,
    }

    protocol_lock  = threading.Lock()
    protocol_state = {
        "in_conflict":              False,
        "mc_station_id":            None,
        "manoeuvre_id":             None,
        "mc_advice":                [],
        "mc_eta_s":                 None,
        "own_target_speed_ms":      None,
        "slowdown_received":        False,
        "slowdown_sender_id":       None,
        "slowdown_delta_time":      None,
        "slowdown_sent":            False,
        "slowdown_sent_to":         None,
        "slowdown_grant_received":  False,
        "slowdown_grant_sender_id": None,
        "slowdown_grant_sent":      False,
        "merge_grant_sent":         False,
        "grants_received":          set(),
        "grant_timeout":            None,
        "merge_decided":            False,
        "slowed_down":              False,
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
                          merge_lat=merge_lat, merge_lon=merge_lon,
                          main_road_ref=main_road, L_main=L_main,
                          demo_mode=demo_mode, demo_step_delay=demo_step_delay),
    )

    try:
        elapsed = 0.0
        while t < 1.0:
            lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
            lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])

            target    = vehicle_state["target_speed_ms"]
            cur_speed = vehicle_state["speed_ms"]
            if cur_speed > target + 0.01:
                cur_speed = max(target, cur_speed - DECELERATION_MS2 * DT)
            elif cur_speed < target - 0.01:
                cur_speed = min(target, cur_speed + ACCELERATION_MS2 * DT)
            vehicle_state["lat"]      = lat
            vehicle_state["lon"]      = lon
            vehicle_state["speed_ms"] = cur_speed
            vehicle_state["bearing"]  = bearing

            cam = build_cam(lat, lon, bearing, cur_speed, road.get("lane_position"))
            vanetza_session.put("vanetza/in/cam", json.dumps(cam).encode())

            should_advance = True

            if is_ramp and main_road is not None:
                last_conflict_set = detect_conflicts(
                    vehicle_id, t, L_m, cur_speed,
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
                        lat, lon, bearing, cur_speed,
                        t, L_m,
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
                                status = build_merge_confirmed(
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
                            status = build_merge_confirmed(
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

            dt_t = cur_speed * DT / L_m
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
            exec_status = build_execution_status(
                own_station_id, lat, lon, manoeuvre_state["manoeuvre_id"],
            )
            vanetza_session.put("vanetza/in/mcm", json.dumps(exec_status).encode())
            if main_road is not None:
                t2 = project_t(lat, lon, main_road)
                t2 = max(0.0, min(t2, 1.0))
                L2 = haversine(
                    main_road["start"]["lat"], main_road["start"]["lon"],
                    main_road["end"]["lat"],   main_road["end"]["lon"],
                )
                bearing2 = compute_bearing(
                    main_road["start"]["lat"], main_road["start"]["lon"],
                    main_road["end"]["lat"],   main_road["end"]["lon"],
                )
                speed2  = vehicle_state["speed_ms"]
                target2 = main_road["speed_limit_kmh"] / 3.6
                ts2 = time.strftime("%H:%M:%S")
                print(f"[{ts2}] [{vehicle_id}] a transitar para via principal em t={t2:.2f}")
                while t2 < 1.0:
                    lat = main_road["start"]["lat"] + t2 * (main_road["end"]["lat"] - main_road["start"]["lat"])
                    lon = main_road["start"]["lon"] + t2 * (main_road["end"]["lon"] - main_road["start"]["lon"])
                    if speed2 < target2 - 0.01:
                        speed2 = min(target2, speed2 + ACCELERATION_MS2 * DT)
                    vehicle_state["lat"]      = lat
                    vehicle_state["lon"]      = lon
                    vehicle_state["speed_ms"] = speed2
                    vehicle_state["bearing"]  = bearing2
                    cam = build_cam(lat, lon, bearing2, speed2, main_road.get("lane_position"))
                    vanetza_session.put("vanetza/in/cam", json.dumps(cam).encode())
                    dt_t2 = speed2 * DT / L2
                    t2 += dt_t2
                    elapsed += DT
                    time.sleep(DT / speed_factor)
                lat = main_road["end"]["lat"]
                lon = main_road["end"]["lon"]
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
