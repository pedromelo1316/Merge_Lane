import json
import random
import threading
import time

from geo import (
    VEHICLE_LENGTH_M, SAFETY_GAP_M,
    road_length, project_t,
    conflict_zone_t, vehicle_in_zone, mc_stop_t, merge_entry_margin,
    find_vehicle_behind, can_brake_in_time,
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
RETRY_COOLDOWN_S       = 0.3   # esperar após recusa antes de reiniciar negociação


class MCProtocol:
    """
    Protocolo do MC (veículo na rampa).
    Envia MERGE_REQUEST, colecta MERGE_GRANTs, decide o merge quando todos os peers activos
    tiverem grantado, e envia MERGE_CONFIRMED + EXECUTION_STATUS.
    """

    def __init__(self, station_id, vehicle_id, ramp, main_road, vanetza_session,
                 vehicle_length_m=VEHICLE_LENGTH_M):
        self.station_id      = station_id
        self.vehicle_id      = vehicle_id
        self.vanetza_session = vanetza_session

        L_ramp = road_length(ramp)
        self.L_ramp    = L_ramp
        self.merge_lat = ramp["end"]["lat"]
        self.merge_lon = ramp["end"]["lon"]

        self.stop_t = mc_stop_t(L_ramp, vehicle_length_m=vehicle_length_m)

        v_mc_ms   = ramp["speed_limit_kmh"] / 3.6
        v_main_ms = main_road["speed_limit_kmh"] / 3.6
        zone_half = vehicle_length_m / 2 + SAFETY_GAP_M + merge_entry_margin(v_mc_ms, v_main_ms)
        self.cz_t_start, self.cz_t_end = conflict_zone_t(
            self.merge_lat, self.merge_lon, main_road,
            before_m=zone_half, after_m=zone_half,
        )
        self.L_main = road_length(main_road)

        # Coordenadas GPS do início e fim da zona de conflito na main road
        s, e = main_road["start"], main_road["end"]
        self.zone_start_lat = s["lat"] + self.cz_t_start * (e["lat"] - s["lat"])
        self.zone_start_lon = s["lon"] + self.cz_t_start * (e["lon"] - s["lon"])
        self.zone_end_lat   = s["lat"] + self.cz_t_end   * (e["lat"] - s["lat"])
        self.zone_end_lon   = s["lon"] + self.cz_t_end   * (e["lon"] - s["lon"])
        self.zone_length_m  = zone_half * 2

        self._lock         = threading.Lock()
        self._manoeuvre_id = 0
        self._request_sent = False
        self._last_send_ts = 0.0
        self._retry_after  = 0.0
        self._lat          = 0.0
        self._lon          = 0.0

        self.grants_received = set()
        self.merge_decided   = False

        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{vehicle_id}] MCProtocol: stop_t={self.stop_t:.3f} "
            f"cz=[{self.cz_t_start:.4f}, {self.cz_t_end:.4f}]"
        )

    def tick(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        """Chamado a cada tick. Retorna {'advance': bool, 'target_speed': float|None}."""
        with self._lock:
            self._lat = lat
            self._lon = lon

        result = {"advance": True, "target_speed": None}

        if self.merge_decided:
            return result

        eta_s = (1.0 - t) * self.L_ramp / speed_ms if speed_ms > 0 else float("inf")

        active_peers = frozenset(neighbours_snapshot.keys())

        if eta_s <= CONFLICT_HORIZON_S:
            if not active_peers and not self.merge_decided:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] sem peers na estrada — merge direto")
                self._on_all_granted(lat, lon)
            else:
                self._maybe_send_merge_request(t, speed_ms, lat, lon, bearing,
                                               eta_s, neighbours_snapshot)

        # verificar se todos os peers activos já concederam
        with self._lock:
            granted = frozenset(self.grants_received)
        if active_peers and active_peers.issubset(granted) and not self.merge_decided:
            self._on_all_granted(lat, lon)

        if t >= self.stop_t and not self.merge_decided:
            result["advance"] = False

        return result

    def _maybe_send_merge_request(self, t, speed_ms, lat, lon, bearing,
                                  eta_s, neighbours_snapshot):
        """Envia MERGE_REQUEST com throttle; respeita cooldown após recusa."""
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
            (sid, max(0.0, (neighbours_snapshot[sid].get("speed_ms") or speed_ms) * 0.7))
            for sid in sorted(neighbours_snapshot)
        ]

        eta_start_ms = max(0, int((eta_s - 2.0) * 1000))
        eta_end_ms   = int((eta_s + 2.0) * 1000)

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
        print(
            f"[{ts}] [{self.vehicle_id}] MERGE_REQUEST enviado "
            f"manoeuvre_id={mid} peers={sorted(neighbours_snapshot)} eta={eta_s:.1f}s"
        )

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
        """Chamado pelo MCM callback quando chega MERGE_GRANT ou recusa."""
        with self._lock:
            if manoeuvre_id != self._manoeuvre_id:
                return  # stale, ignorar
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

    def on_merge_completed(self, lat, lon):
        """Chamado pela simulation após transição para a main road — envia EXECUTION_STATUS."""
        with self._lock:
            mid = self._manoeuvre_id
        status = build_execution_status(self.station_id, lat, lon, mid)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(status).encode())
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] EXECUTION_STATUS enviado")

    def make_mcm_callback(self):
        """Retorna subscriber closure para vanetza/out/mcm (apenas MERGE_GRANT interessa ao MC)."""
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

                # MERGE_GRANT: mcmType=2, itssRole=3, manoeuvreId em [0,127]
                if mcm_type == 2 and its_role == 3 and manoeuvre_id < 128:
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self.on_merge_grant(sender_id, manoeuvre_id, success=(response == 0))
            except Exception as exc:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] MCProtocol MCM erro: {exc}")
        return on_mcm


# ---------------------------------------------------------------------------

class RoadVehicleProtocol:
    """
    Protocolo dos veículos na main road.
    Recebe MERGE_REQUEST, propaga SLOWDOWN_REQUEST na cadeia, responde com SLOWDOWN_GRANT,
    aplica abrandamento após MERGE_CONFIRMED e retoma velocidade ao receber EXECUTION_STATUS.
    """

    def __init__(self, station_id, vehicle_id, road, merge_lat, merge_lon,
                 vanetza_session, road_speed_ms, neighbours):
        self.station_id    = station_id
        self.vehicle_id    = vehicle_id
        self.road          = road
        self.vanetza_session = vanetza_session
        self.road_speed_ms = road_speed_ms
        self.neighbours    = neighbours

        L_main = road_length(road)
        self.L_main = L_main

        zone_half = VEHICLE_LENGTH_M / 2 + SAFETY_GAP_M
        self.cz_t_start, self.cz_t_end = conflict_zone_t(
            merge_lat, merge_lon, road,
            before_m=zone_half, after_m=zone_half,
        )

        self._lock = threading.Lock()

        # snapshot cinemático — actualizado por tick()
        self._t       = 0.0
        self._lat     = 0.0
        self._lon     = 0.0
        self._speed_ms = road_speed_ms
        self._bearing  = 0.0

        # estado do protocolo
        self._pending_speed      = None   # velocidade a aplicar após MERGE_CONFIRMED
        self._slowed_down        = False
        self._manoeuvre_id       = None
        self._mc_station_id      = None
        self._slowdown_sender_id = None   # quem nos enviou SLOWDOWN_REQUEST (None = 1º da cadeia)
        self._slowdown_sent_to   = None   # a quem reencaminámos SLOWDOWN_REQUEST

        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{vehicle_id}] RoadVehicleProtocol: "
            f"cz=[{self.cz_t_start:.4f}, {self.cz_t_end:.4f}]"
        )

    # ── interface chamada pela simulation ────────────────────────────────────

    def tick(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        """Actualiza estado cinemático; retorna target_speed override enquanto abrandado."""
        with self._lock:
            self._t        = t
            self._lat      = lat
            self._lon      = lon
            self._speed_ms = speed_ms
            self._bearing  = bearing
            slowed  = self._slowed_down
            pending = self._pending_speed

        if slowed and pending is not None:
            return {"advance": True, "target_speed": pending}
        return {"advance": True, "target_speed": None}

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
                    self._on_merge_request(sender_id, manoeuvre_id, inner)

                # SLOWDOWN_REQUEST: veículo → veículo (mcmType=1, itssRole=3)
                elif mcm_type == 1 and its_role == 3:
                    vmc    = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
                    advice = vmc.get("manoeuvreAdvice", [])
                    if not advice or advice[0].get("executantID") != self.station_id:
                        return
                    try:
                        sugg = advice[0]["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                    except (KeyError, IndexError):
                        sugg = None
                    self._on_slowdown_request(sender_id, manoeuvre_id, sugg)

                # SLOWDOWN_GRANT/REFUSE: veículo atrás → nós (mcmType=2, itssRole=3, id>=128)
                elif mcm_type == 2 and its_role == 3 and manoeuvre_id >= 128:
                    with self._lock:
                        expected = self._slowdown_sent_to
                    if sender_id != expected:
                        return
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self._on_slowdown_grant(success=(response == 0))

                # MERGE_CONFIRMED: MC → estrada (mcmType=2, itssRole=1)
                elif mcm_type == 2 and its_role == 1:
                    response = inner["mcmContainer"]["responseContainer"]["manouevreResponse"]
                    self._on_merge_confirmed(success=(response == 0))

                # EXECUTION_STATUS: MC → estrada (mcmType=7, itssRole=1)
                elif mcm_type == 7 and its_role == 1:
                    self._on_execution_status()

            except Exception as exc:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] RoadVehicleProtocol MCM erro: {exc}")
        return on_mcm

    # ── handlers de MCM ──────────────────────────────────────────────────────

    def _on_merge_request(self, sender_id, manoeuvre_id, inner):
        with self._lock:
            self._mc_station_id      = sender_id
            self._manoeuvre_id       = manoeuvre_id
            self._slowdown_sender_id = None
            self._slowdown_sent_to   = None
            self._pending_speed      = None
            t       = self._t
            speed   = self._speed_ms

        # ETA do MC ao merge point
        vmc    = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
        subs   = vmc.get("submaneuvres", [])
        temporal = subs[0].get("temporalCharateristics", {}) if subs else {}
        t_start  = temporal.get("tRROccupancyStartTime", 2000)
        t_end    = temporal.get("tRROccupancyEndTime",   5000)
        mc_eta_s = (t_start + t_end) / 2 / 1000.0

        # Zona de conflito: usa o TRR enviado pelo MC; fallback para valor local
        mc_pos = inner["basicContainer"]["position"]
        mc_lat = mc_pos["latitude"]
        mc_lon = mc_pos["longitude"]
        cz_t_start = self.cz_t_start
        cz_t_end   = self.cz_t_end
        trr = subs[0].get("targetRoadResourceIContainer") if subs else None
        if trr:
            wps = trr.get("waypoints", [])
            if len(wps) >= 2:
                d0 = wps[0]["pathPosition"]
                d1 = wps[1]["pathPosition"]
                zs_lat = mc_lat + d0["deltaLatitude"]
                zs_lon = mc_lon + d0["deltaLongitude"]
                ze_lat = mc_lat + d1["deltaLatitude"]
                ze_lon = mc_lon + d1["deltaLongitude"]
                cz_t_start = project_t(zs_lat, zs_lon, self.road)
                cz_t_end   = project_t(ze_lat, ze_lon, self.road)
                with self._lock:
                    self.cz_t_start = cz_t_start
                    self.cz_t_end   = cz_t_end

        # posição prevista no instante do merge
        t_pred      = min(t + speed * mc_eta_s / self.L_main, 1.0)
        in_conflict = vehicle_in_zone(t_pred, self.L_main, VEHICLE_LENGTH_M,
                                      cz_t_start, cz_t_end)

        # velocidade sugerida pelo MC para este veículo
        advice = vmc.get("manoeuvreAdvice", [])
        sugg_speed = self._extract_suggested_speed(advice, speed)

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] MERGE_REQUEST de MC={sender_id} "
              f"eta={mc_eta_s:.1f}s in_conflict={in_conflict}")

        if not in_conflict:
            self._send_merge_grant(success=True)
            return

        snap   = self.neighbours.snapshot()
        behind = find_vehicle_behind(t, self.station_id, self.road, snap)

        if behind is not None:
            behind_id = behind[0]
            with self._lock:
                self._slowdown_sent_to = behind_id
            self._send_slowdown_request(behind_id, sugg_speed, manoeuvre_id)
        else:
            # fim de cadeia — verificar viabilidade de travagem
            avail = self._available_dist()
            ok, brake_d = can_brake_in_time(speed, sugg_speed, avail)
            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"[{ts}] [{self.vehicle_id}] aceita (dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                      f"— pendente {sugg_speed * 3.6:.1f} km/h")
                with self._lock:
                    self._pending_speed = sugg_speed
                self._send_merge_grant(success=True)
            else:
                print(f"[{ts}] [{self.vehicle_id}] recusa (dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
                self._send_merge_grant(success=False)

    def _on_slowdown_request(self, sender_id, manoeuvre_id, sugg_speed):
        with self._lock:
            self._slowdown_sender_id = sender_id
            self._manoeuvre_id       = manoeuvre_id
            t     = self._t
            speed = self._speed_ms
            if sugg_speed is None:
                sugg_speed = speed * 0.7

        snap   = self.neighbours.snapshot()
        with self._lock:
            mc_id = self._mc_station_id
        behind = find_vehicle_behind(t, self.station_id, self.road, snap)

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_REQUEST de {sender_id} "
              f"sugg={sugg_speed * 3.6:.1f} km/h")

        if behind is not None:
            behind_id = behind[0]
            with self._lock:
                self._slowdown_sent_to = behind_id
            self._send_slowdown_request(behind_id, sugg_speed, manoeuvre_id)
        else:
            avail = self._available_dist()
            ok, brake_d = can_brake_in_time(speed, sugg_speed, avail)
            ts = time.strftime("%H:%M:%S")
            if ok:
                print(f"[{ts}] [{self.vehicle_id}] aceita fim-de-cadeia "
                      f"(dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                      f"— pendente {sugg_speed * 3.6:.1f} km/h")
                with self._lock:
                    self._pending_speed = sugg_speed
                self._send_slowdown_grant(sender_id, success=True)
            else:
                print(f"[{ts}] [{self.vehicle_id}] recusa fim-de-cadeia "
                      f"(dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
                self._send_slowdown_grant(sender_id, success=False)

    def _on_slowdown_grant(self, success):
        with self._lock:
            sender_ahead = self._slowdown_sender_id
            speed        = self._speed_ms
            pending      = self._pending_speed
            mc_id        = self._mc_station_id
            mid          = self._manoeuvre_id

        ts = time.strftime("%H:%M:%S")

        if not success:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT(recusa) — propagar recusa")
            if sender_ahead is not None:
                self._send_slowdown_grant(sender_ahead, success=False)
            else:
                self._send_merge_grant(success=False)
            return

        # grant da cadeia — verificar própria viabilidade
        sugg_speed = pending if pending is not None else speed * 0.7
        avail = self._available_dist()
        ok, brake_d = can_brake_in_time(speed, sugg_speed, avail)

        if ok:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT recebido — própria travagem ok "
                  f"(dist_trav={brake_d:.1f}m avail={avail:.1f}m) "
                  f"— pendente {sugg_speed * 3.6:.1f} km/h")
            with self._lock:
                self._pending_speed = sugg_speed
            if sender_ahead is not None:
                self._send_slowdown_grant(sender_ahead, success=True)
            else:
                self._send_merge_grant(success=True)
        else:
            print(f"[{ts}] [{self.vehicle_id}] SLOWDOWN_GRANT recebido mas própria travagem falha "
                  f"(dist_trav={brake_d:.1f}m > avail={avail:.1f}m)")
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

    # ── helpers internos ─────────────────────────────────────────────────────

    def _available_dist(self):
        """Metros desde posição actual até ao início da zona de conflito."""
        with self._lock:
            t          = self._t
            cz_t_start = self.cz_t_start
        return max(0.0, (cz_t_start - t) * self.L_main - VEHICLE_LENGTH_M / 2)

    def _extract_suggested_speed(self, advice, fallback_speed):
        """Extrai velocidade sugerida do MC para este veículo; fallback 70% da vel. actual."""
        for entry in advice:
            if entry.get("executantID") == self.station_id:
                try:
                    return entry["submaneuvres"][0]["advisedTrajectory"]["speed"][0]["speedValue"]
                except (KeyError, IndexError):
                    pass
        return fallback_speed * 0.7

    def _send_merge_grant(self, success=True):
        with self._lock:
            mc_id = self._mc_station_id
            mid   = self._manoeuvre_id
            lat   = self._lat
            lon   = self._lon
        msg = build_merge_grant(self.station_id, lat, lon, mid, success=success)
        self.vanetza_session.put("vanetza/in/mcm", json.dumps(msg).encode())
        ts = time.strftime("%H:%M:%S")
        label = "MERGE_GRANT" if success else "MERGE_GRANT(recusa)"
        print(f"[{ts}] [{self.vehicle_id}] {label} → MC stationID={mc_id}")

    def _send_slowdown_request(self, target_id, suggested_speed_ms, manoeuvre_id):
        with self._lock:
            lat     = self._lat
            lon     = self._lon
            heading = self._bearing
            speed   = self._speed_ms
        msg = build_slowdown_request(
            station_id        = self.station_id,
            lat               = lat,
            lon               = lon,
            heading           = heading,
            speed_ms          = speed,
            manoeuvre_id      = manoeuvre_id,
            next_vehicle_id   = target_id,
            suggested_speed_ms = suggested_speed_ms,
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
