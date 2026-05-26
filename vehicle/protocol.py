import json
import random
import threading
import time

from geo import (
    SAFETY_GAP_M,
    road_length, project_t,
    conflict_zone_t, vehicle_in_zone, mc_stop_t, merge_entry_margin,
    find_vehicle_behind, can_brake_in_time,
    gap_ahead, is_on_road,
)
from mcm_builder import (
    build_merge_request,
    build_merge_confirmed,
    build_execution_status,
    build_slowdown_request,
    build_slowdown_grant,
    build_merge_grant,
)

CONFLICT_HORIZON_S     = 4.0   # segundos antes do merge point para iniciar negociação
MERGE_REQUEST_RESEND_S = 1.0   # reenviar MERGE_REQUEST se sem grants após X segundos
RETRY_COOLDOWN_S       = 1.0   # esperar após recusa antes de reiniciar negociação


class MergeProtocol:
    """
    Protocolo unificado para todos os veículos.

    - Se estiver na rampa com merges_into: comporta-se como MC (envia MERGE_REQUEST).
    - Se estiver na main road com rampa a entrar: responde a MERGE_REQUEST e gere cadeia.
    """

    def __init__(self, station_id, vehicle_id, road, ramp_road, main_road,
                 vanetza_session, road_speed_ms, neighbours, vehicle_length_m):
        self.station_id       = station_id
        self.vehicle_id       = vehicle_id
        self.vanetza_session  = vanetza_session
        self.vehicle_length_m = vehicle_length_m
        self.neighbours       = neighbours

        self.ramp_road = ramp_road
        self.main_road = main_road

        self._lock = threading.Lock()

        # Road context (actualizado quando o veículo muda de estrada)
        self.current_road     = None
        self.current_is_ramp  = False
        self.L_current        = 0.0
        self.road_speed_ms    = road_speed_ms
        self.set_current_road(road, road_speed_ms)

        # Geometria do merge (para MC) — calculada se houver rampa+main
        self.L_ramp          = None
        self.L_main          = None
        self.merge_lat       = None
        self.merge_lon       = None
        self.stop_t          = None
        self.after_m         = None
        self.v_main_ms       = None
        self.mc_cz_t_start   = None
        self.mc_cz_t_end     = None
        self.zone_start_lat  = None
        self.zone_start_lon  = None
        self.zone_end_lat    = None
        self.zone_end_lon    = None
        self.zone_length_m   = None
        self._init_merge_geometry()

        # Estado MC
        self._manoeuvre_id = 0
        self._request_sent = False
        self._last_send_ts = 0.0
        self._retry_after  = 0.0
        self._lat          = 0.0
        self._lon          = 0.0
        self.grants_received = set()
        self.merge_decided   = False

        # Estado road-vehicle
        self.cz_t_start = None
        self.cz_t_end   = None
        self._t         = 0.0
        self._speed_ms  = road_speed_ms
        self._bearing   = 0.0

        self._pending_speed      = None
        self._slowed_down        = False
        self._mc_station_id      = None
        self._mc_eta_s           = None
        self._slowdown_sender_id = None
        self._slowdown_sent_to   = None

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] MergeProtocol ready")

    # ── public interface ──────────────────────────────────────────────────

    def set_current_road(self, road, road_speed_ms):
        with self._lock:
            self.current_road    = road
            self.current_is_ramp = bool(road.get("type") == "ramp" and road.get("merges_into"))
            self.L_current       = road_length(road)
            self.road_speed_ms   = road_speed_ms

    def tick(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        """Chamado a cada tick. Retorna {'advance': bool, 'target_speed': float|None}."""
        with self._lock:
            self._t        = t
            self._lat      = lat
            self._lon      = lon
            self._speed_ms = speed_ms
            self._bearing  = bearing

        result = {"advance": True, "target_speed": None}

        if self._is_ramp() and self._has_merge():
            result = self._tick_ramp(t, speed_ms, lat, lon, bearing, neighbours_snapshot)

        with self._lock:
            slowed  = self._slowed_down
            pending = self._pending_speed

        if not self._is_ramp() and slowed and pending is not None:
            result["target_speed"] = pending

        return result

    def on_merge_completed(self, lat, lon):
        """Chamado após transição para a main road — envia EXECUTION_STATUS."""
        with self._lock:
            mid = self._manoeuvre_id
        status = build_execution_status(self.station_id, lat, lon, mid)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(status).encode())
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] EXECUTION_STATUS enviado")

    def make_mcm_callback(self):
        """Retorna subscriber closure para vanetza/out/mcm."""
        def on_mcm(sample):
            try:
                payload      = json.loads(bytes(sample.payload).decode())
                sender_id    = payload.get("stationID") or payload.get("stationId")
                if sender_id == self.station_id:
                    return
                inner        = payload["fields"]["payload"]
                basic        = inner["basicContainer"]
                mcm_type     = basic["mcmType"]
                its_role     = basic.get("itssRole", 0)
                manoeuvre_id = basic["manoeuvreId"]

                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] MCM de stationID={sender_id} "
                      f"mcmType={mcm_type} itssRole={its_role}")

                # MERGE_REQUEST: MC → estrada (mcmType=1, itssRole=1)
                if mcm_type == 1 and its_role == 1:
                    if self._is_ramp():
                        return
                    self._on_merge_request(sender_id, manoeuvre_id, inner)
                    return

                # SLOWDOWN_REQUEST: veículo → veículo (mcmType=1, itssRole=3)
                if mcm_type == 1 and its_role == 3:
                    if self._is_ramp():
                        return
                    vmc    = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
                    advice = vmc.get("manoeuvreAdvice", [])
                    if not advice or advice[0].get("executantID") != self.station_id:
                        return
                    try:
                        sugg = advice[0]["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                    except (KeyError, IndexError):
                        sugg = None
                    self._on_slowdown_request(sender_id, manoeuvre_id, sugg)
                    return

                # MERGE_GRANT: estrada → MC (mcmType=2, itssRole=3, id<128)
                if mcm_type == 2 and its_role == 3 and manoeuvre_id < 128:
                    if not self._is_ramp():
                        return
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self.on_merge_grant(sender_id, manoeuvre_id, success=(response == 0))
                    return

                # SLOWDOWN_GRANT/REFUSE: veículo atrás → nós (mcmType=2, itssRole=3, id>=128)
                if mcm_type == 2 and its_role == 3 and manoeuvre_id >= 128:
                    if self._is_ramp():
                        return
                    with self._lock:
                        expected = self._slowdown_sent_to
                    if sender_id != expected:
                        return
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self._on_slowdown_grant(success=(response == 0))
                    return

                # MERGE_CONFIRMED: MC → estrada (mcmType=2, itssRole=1)
                if mcm_type == 2 and its_role == 1:
                    if self._is_ramp():
                        return
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self._on_merge_confirmed(success=(response == 0))
                    return

                # EXECUTION_STATUS: MC → estrada (mcmType=7, itssRole=1)
                if mcm_type == 7 and its_role == 1:
                    if self._is_ramp():
                        return
                    self._on_execution_status()
                    return

            except Exception as exc:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] MergeProtocol MCM erro: {exc}")
        return on_mcm

    # ── MC logic (rampa) ─────────────────────────────────────────────────

    def _tick_ramp(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        result = {"advance": True, "target_speed": None}

        if self.merge_decided:
            return result

        eta_s = (1.0 - t) * self.L_current / speed_ms if speed_ms > 0 else float("inf")

        has_ahead = False
        if neighbours_snapshot:
            gap = gap_ahead(t, self.station_id, self.current_road,
                            neighbours_snapshot, self.L_current, self.vehicle_length_m)
            has_ahead = gap != float("inf")

        main_peers = {}
        if self.main_road is not None:
            main_peers = {
                sid: state for sid, state in neighbours_snapshot.items()
                if is_on_road(state, self.main_road)
            }
        active_peers = frozenset(main_peers.keys())

        if eta_s <= CONFLICT_HORIZON_S and not has_ahead:
            if not active_peers and not self.merge_decided:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] sem peers na estrada — merge direto")
                self._on_all_granted(lat, lon)
            else:
                self._maybe_send_merge_request(t, speed_ms, lat, lon, bearing,
                                               eta_s, main_peers)

        with self._lock:
            granted = frozenset(self.grants_received)
        if active_peers and active_peers.issubset(granted) and not self.merge_decided and not has_ahead:
            self._on_all_granted(lat, lon)

        if self.stop_t is not None and t >= self.stop_t and not self.merge_decided:
            result["advance"] = False

        return result

    def _maybe_send_merge_request(self, t, speed_ms, lat, lon, bearing,
                                  eta_s, neighbours_snapshot):
        """Envia MERGE_REQUEST com throttle; respeita cooldown após recusa."""
        if self.zone_start_lat is None or self.zone_end_lat is None:
            return
        now = time.time()
        with self._lock:
            already_sent = self._request_sent
            last_ts      = self._last_send_ts
            retry_after  = self._retry_after

        if now < retry_after:
            return
        if already_sent and (now - last_ts) < MERGE_REQUEST_RESEND_S:
            return
        if not neighbours_snapshot:
            return

        conflict_vehicles = [
            (sid, neighbours_snapshot[sid].get("speed_ms") or speed_ms)
            for sid in sorted(neighbours_snapshot)
        ]

        occupancy_s  = self.after_m / self.v_main_ms if self.v_main_ms else 0.0
        eta_start_ms = int(eta_s * 1000)
        eta_end_ms   = int((eta_s + occupancy_s) * 1000)

        with self._lock:
            self._manoeuvre_id = (self._manoeuvre_id + 1) % 128
            mid = self._manoeuvre_id

        mcm = build_merge_request(
            station_id        = self.station_id,
            lat               = lat,
            lon               = lon,
            heading           = bearing,
            speed_ms          = speed_ms,
            manoeuvre_id      = mid,
            conflict_vehicles = conflict_vehicles,
            vehicle_length_m  = self.vehicle_length_m,
            eta_start_ms      = eta_start_ms,
            eta_end_ms        = eta_end_ms,
            zone_start_lat    = self.zone_start_lat,
            zone_start_lon    = self.zone_start_lon,
            zone_end_lat      = self.zone_end_lat,
            zone_end_lon      = self.zone_end_lon,
            zone_length_m     = self.zone_length_m,
        )
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(mcm).encode())

        with self._lock:
            self._request_sent = True
            self._last_send_ts = now

        ts = time.strftime("%H:%M:%S")
        arrival_ts = time.strftime("%H:%M:%S", time.localtime(time.time() + eta_s))
        print(
            f"[{ts}] [{self.vehicle_id}] MERGE_REQUEST enviado "
            f"manoeuvre_id={mid} peers={sorted(neighbours_snapshot)} eta={eta_s:.1f}s"
        )
        print(f"[{ts}] [{self.vehicle_id}] DEBUG: MERGE_REQUEST | pos=({lat:.6f},{lon:.6f}) "
              f"| eta_chegada={arrival_ts} ({eta_s:.1f}s) | zona_conflito=({self.zone_start_lat:.6f},"
              f"{self.zone_start_lon:.6f})→({self.zone_end_lat:.6f},{self.zone_end_lon:.6f}) "
              f"| merge_point=({self.merge_lat:.6f},{self.merge_lon:.6f})")

    def _on_all_granted(self, lat, lon):
        """Todos os peers activos concederam — envia MERGE_CONFIRMED e decide o merge."""
        with self._lock:
            if self.merge_decided:
                return
            self.merge_decided = True
            mid = self._manoeuvre_id

        confirmed = build_merge_confirmed(self.station_id, lat, lon, mid, success=True)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(confirmed).encode())
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] MERGE_CONFIRMED(agree) enviado — a executar merge")

    def on_merge_grant(self, sender_id, manoeuvre_id, success):
        """Chamado quando chega MERGE_GRANT ou recusa."""
        with self._lock:
            if manoeuvre_id != self._manoeuvre_id:
                return
            if not success:
                self._request_sent = False
                self.grants_received.clear()
                self._retry_after = time.time() + RETRY_COOLDOWN_S
                lat, lon = self._lat, self._lon
                mid = self._manoeuvre_id
            else:
                self.grants_received.add(sender_id)
                return

        abort = build_merge_confirmed(self.station_id, lat, lon, mid, success=False)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(abort).encode())
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{self.vehicle_id}] MERGE_GRANT(recusa) de {sender_id} — "
            f"MERGE_CONFIRMED(abort) enviado; retry em {RETRY_COOLDOWN_S}s"
        )

    # ── Road-vehicle logic (main) ─────────────────────────────────────────

    def _on_merge_request(self, sender_id, manoeuvre_id, inner):
        with self._lock:
            self._mc_station_id      = sender_id
            self._manoeuvre_id       = manoeuvre_id
            self._slowdown_sender_id = None
            self._slowdown_sent_to   = None
            self._pending_speed      = None
            t       = self._t
            speed   = self._speed_ms

        vmc    = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
        subs   = vmc.get("submaneuvres", [])
        temporal = subs[0].get("temporalCharateristics", {}) if subs else {}
        t_start  = temporal.get("tRROccupancyStartTime", 0)
        mc_eta_s = t_start / 1000.0
        with self._lock:
            self._mc_eta_s = mc_eta_s

        mc_size  = vmc.get("vehicleCurrentStateContainer", {}).get("vehicleSize", {})
        mc_len_m = mc_size["vehicleLenth"]["vehicleLengthValue"]

        mc_pos = inner["basicContainer"]["position"]
        mc_lat = mc_pos["latitude"]
        mc_lon = mc_pos["longitude"]
        trr = subs[0].get("targetRoadResourceIContainer") if subs else None
        wps = trr.get("waypoints", []) if trr else []
        if len(wps) < 2:
            self._send_merge_grant(success=False)
            return

        d0 = wps[0]["pathPosition"]
        d1 = wps[1]["pathPosition"]
        zs_lat = mc_lat + d0["deltaLatitude"]
        zs_lon = mc_lon + d0["deltaLongitude"]
        ze_lat = mc_lat + d1["deltaLatitude"]
        ze_lon = mc_lon + d1["deltaLongitude"]
        cz_t_start = project_t(zs_lat, zs_lon, self.current_road)
        cz_t_end   = project_t(ze_lat, ze_lon, self.current_road)
        with self._lock:
            self.cz_t_start = cz_t_start
            self.cz_t_end   = cz_t_end

        t_pred      = min(t + speed * mc_eta_s / self.L_current, 1.0)
        in_zone_pred = vehicle_in_zone(t_pred, self.L_current, self.vehicle_length_m,
                           cz_t_start, cz_t_end)
        in_conflict = in_zone_pred

        own_speed = self._calculate_target_speed(mc_eta_s)

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] MERGE_REQUEST de MC={sender_id} "
              f"eta={mc_eta_s:.1f}s in_conflict={in_conflict} "
              f"v_alvo={own_speed * 3.6:.1f} km/h")

        if not in_conflict:
            self._send_merge_grant(success=True)
            return

        with self._lock:
            self._pending_speed = own_speed

        snap   = self.neighbours.snapshot()
        behind = find_vehicle_behind(t, self.station_id, self.current_road, snap)

        if behind is not None:
            behind_id = behind[0]
            with self._lock:
                self._slowdown_sent_to = behind_id
            self._send_slowdown_request(behind_id, own_speed, manoeuvre_id)
        else:
            avail = self._available_dist()
            ok, brake_d = can_brake_in_time(speed, own_speed, avail)
            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"[{ts}] [{self.vehicle_id}] aceita (dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                      f"— pendente {own_speed * 3.6:.1f} km/h")
                self._send_merge_grant(success=True)
            else:
                print(f"[{ts}] [{self.vehicle_id}] recusa (dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
                with self._lock:
                    self._pending_speed = None
                self._send_merge_grant(success=False)

    def _on_slowdown_request(self, sender_id, manoeuvre_id, sugg_speed):
        with self._lock:
            self._slowdown_sender_id = sender_id
            self._manoeuvre_id       = manoeuvre_id
            t        = self._t
            speed    = self._speed_ms

        if sugg_speed is not None:
            own_speed = sugg_speed
        else:
            own_speed = speed * 0.7

        snap   = self.neighbours.snapshot()
        behind = find_vehicle_behind(t, self.station_id, self.current_road, snap)

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_REQUEST de {sender_id} "
              f"v_alvo={own_speed * 3.6:.1f} km/h")

        with self._lock:
            self._pending_speed = own_speed

        if behind is not None:
            behind_id = behind[0]
            with self._lock:
                self._slowdown_sent_to = behind_id
            self._send_slowdown_request(behind_id, own_speed, manoeuvre_id)
        else:
            avail = self._available_dist()
            ok, brake_d = can_brake_in_time(speed, own_speed, avail)
            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"[{ts}] [{self.vehicle_id}] aceita fim-de-cadeia "
                      f"(dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                      f"— pendente {own_speed * 3.6:.1f} km/h")
                self._send_slowdown_grant(sender_id, success=True)
            else:
                print(f"[{ts}] [{self.vehicle_id}] recusa fim-de-cadeia "
                      f"(dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
                with self._lock:
                    self._pending_speed = None
                self._send_slowdown_grant(sender_id, success=False)

    def _on_slowdown_grant(self, success):
        with self._lock:
            sender_ahead = self._slowdown_sender_id
            speed        = self._speed_ms
            pending      = self._pending_speed

        ts = time.strftime("%H:%M:%S")

        if not success:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT(recusa) — propagar recusa")
            with self._lock:
                self._pending_speed = None
            if sender_ahead is not None:
                self._send_slowdown_grant(sender_ahead, success=False)
            else:
                self._send_merge_grant(success=False)
            return

        own_speed = pending if pending is not None else speed * 0.7
        avail = self._available_dist()
        ok, brake_d = can_brake_in_time(speed, own_speed, avail)

        if ok:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT recebido — própria travagem ok "
                  f"(dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                  f"— pendente {own_speed * 3.6:.1f} km/h")
            if sender_ahead is not None:
                self._send_slowdown_grant(sender_ahead, success=True)
            else:
                self._send_merge_grant(success=True)
        else:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT recebido mas própria travagem falha "
                  f"(dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
            with self._lock:
                self._pending_speed = None
            if sender_ahead is not None:
                self._send_slowdown_grant(sender_ahead, success=False)
            else:
                self._send_merge_grant(success=False)

    def _on_merge_confirmed(self, success):
        ts = time.strftime("%H:%M:%S")
        if success:
            with self._lock:
                pending = self._pending_speed
                self._slowed_down = True
            print(f"[{ts}] [{self.vehicle_id}] MERGE_CONFIRMED — a abrandar para "
                  f"{(pending or 0) * 3.6:.1f} km/h")
        else:
            with self._lock:
                self._pending_speed = None
                self._slowed_down   = False
            print(f"[{ts}] [{self.vehicle_id}] MERGE_CONFIRMED(abort) — a cancelar abrandamento")

    def _on_execution_status(self):
        with self._lock:
            self._slowed_down   = False
            self._pending_speed = None
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] EXECUTION_STATUS — a retomar velocidade normal")

    # ── helpers internos ─────────────────────────────────────────────────

    def _init_merge_geometry(self):
        if not self.ramp_road or not self.main_road:
            return

        self.L_ramp    = road_length(self.ramp_road)
        self.L_main    = road_length(self.main_road)
        self.merge_lat = self.ramp_road["end"]["lat"]
        self.merge_lon = self.ramp_road["end"]["lon"]

        self.stop_t = mc_stop_t(self.L_ramp, vehicle_length_m=self.vehicle_length_m)

        v_mc_ms   = self.ramp_road["speed_limit_kmh"] / 3.6
        self.v_main_ms = self.main_road["speed_limit_kmh"] / 3.6
        before_m  = self.vehicle_length_m / 2 + SAFETY_GAP_M + merge_entry_margin(v_mc_ms, self.v_main_ms)
        after_m   = self.vehicle_length_m / 2 + SAFETY_GAP_M
        self.after_m = after_m

        self.mc_cz_t_start, self.mc_cz_t_end = conflict_zone_t(
            self.merge_lat, self.merge_lon, self.main_road,
            before_m=before_m, after_m=after_m,
        )

        s, e = self.main_road["start"], self.main_road["end"]
        self.zone_start_lat = s["lat"] + self.mc_cz_t_start * (e["lat"] - s["lat"])
        self.zone_start_lon = s["lon"] + self.mc_cz_t_start * (e["lon"] - s["lon"])
        self.zone_end_lat   = s["lat"] + self.mc_cz_t_end   * (e["lat"] - s["lat"])
        self.zone_end_lon   = s["lon"] + self.mc_cz_t_end   * (e["lon"] - s["lon"])
        self.zone_length_m  = before_m + after_m

        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{self.vehicle_id}] MergeGeometry: stop_t={self.stop_t:.3f} "
            f"cz=[{self.mc_cz_t_start:.4f}, {self.mc_cz_t_end:.4f}]"
        )

    def _available_dist(self):
        """Metros desde posição actual até ao início da zona de conflito."""
        with self._lock:
            t          = self._t
            cz_t_start = self.cz_t_start
        if cz_t_start is None:
            return 0.0
        dist = max(0.0, (cz_t_start - t) * self.L_current - self.vehicle_length_m / 2)
        return dist

    def _calculate_target_speed(self, mc_eta_s):
        """Velocidade para chegar ao limite da zona de conflito exatamente no ETA do MC."""
        with self._lock:
            t          = self._t
            cz_t_start = self.cz_t_start
        if cz_t_start is None:
            return 0.0
        dist = max(0.0, (cz_t_start - t) * self.L_current - self.vehicle_length_m / 2)
        v = dist / mc_eta_s if mc_eta_s > 0 else 0.0
        return v

    def _send_merge_grant(self, success=True):
        with self._lock:
            mc_id      = self._mc_station_id
            mid        = self._manoeuvre_id
            lat        = self._lat
            lon        = self._lon
            t          = self._t
            speed      = self._speed_ms
            mc_eta_s   = self._mc_eta_s or 0.0
            cz_t_start = self.cz_t_start
            cz_t_end   = self.cz_t_end
        msg = build_merge_grant(self.station_id, lat, lon, mid, success=success)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(msg).encode())
        ts = time.strftime("%H:%M:%S")
        label = "MERGE_GRANT" if success else "MERGE_GRANT(recusa)"
        print(f"[{ts}] [{self.vehicle_id}] {label} → MC stationID={mc_id}")
        if success and cz_t_start is not None and cz_t_end is not None:
            s_r, e_r = self.current_road["start"], self.current_road["end"]
            zs_lat = s_r["lat"] + cz_t_start * (e_r["lat"] - s_r["lat"])
            zs_lon = s_r["lon"] + cz_t_start * (e_r["lon"] - s_r["lon"])
            ze_lat = s_r["lat"] + cz_t_end   * (e_r["lat"] - s_r["lat"])
            ze_lon = s_r["lon"] + cz_t_end   * (e_r["lon"] - s_r["lon"])
            t_pred   = min(t + speed * mc_eta_s / self.L_current, 1.0) if mc_eta_s > 0 else t
            pred_lat = s_r["lat"] + t_pred * (e_r["lat"] - s_r["lat"])
            pred_lon = s_r["lon"] + t_pred * (e_r["lon"] - s_r["lon"])
            pred_ts  = time.strftime("%H:%M:%S", time.localtime(time.time() + mc_eta_s))
            print(f"[{ts}] [{self.vehicle_id}] DEBUG: MERGE_GRANT(OK) | pos=({lat:.6f},{lon:.6f}) "
                  f"| zona_conflito=({zs_lat:.6f},{zs_lon:.6f})→({ze_lat:.6f},{ze_lon:.6f}) "
                  f"| pos_prevista=({pred_lat:.6f},{pred_lon:.6f}) | ts_previsao={pred_ts}")

    def _send_slowdown_request(self, target_id, suggested_speed_ms, manoeuvre_id):
        with self._lock:
            lat     = self._lat
            lon     = self._lon
            heading = self._bearing
            speed   = self._speed_ms
        msg = build_slowdown_request(
            station_id         = self.station_id,
            lat                = lat,
            lon                = lon,
            heading            = heading,
            speed_ms           = speed,
            manoeuvre_id       = manoeuvre_id,
            next_vehicle_id    = target_id,
            suggested_speed_ms = suggested_speed_ms,
            vehicle_length_m   = self.vehicle_length_m,
        )
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(msg).encode())
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_REQUEST → stationID={target_id} "
              f"sugg={suggested_speed_ms * 3.6:.1f} km/h")

    def _send_slowdown_grant(self, target_id, success=True):
        with self._lock:
            lat = self._lat
            lon = self._lon
        grant_id = random.randint(128, 255)
        msg = build_slowdown_grant(self.station_id, lat, lon, grant_id, success=success)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(msg).encode())
        ts = time.strftime("%H:%M:%S")
        label = "SLOWDOWN_GRANT" if success else "SLOWDOWN_GRANT(recusa)"
        print(f"[{ts}] [{self.vehicle_id}] {label} → stationID={target_id}")

    def _is_ramp(self):
        with self._lock:
            return self.current_is_ramp

    def _has_merge(self):
        return self.ramp_road is not None and self.main_road is not None