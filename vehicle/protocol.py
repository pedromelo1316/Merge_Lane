import json
import threading
import time

from geo import (
    VEHICLE_LENGTH_M, SAFETY_GAP_M,
    road_length,
    conflict_zone_t, mc_stop_t,
)
from mcm_builder import build_merge_request

CONFLICT_HORIZON_S     = 6.0  # segundos antes do merge point para iniciar negociação
MERGE_REQUEST_RESEND_S = 3.0  # reenviar MERGE_REQUEST se sem grants após X segundos


class MCProtocol:
    """
    MC-side merge protocol — Task 4.
    Handles: ETA calculation, MERGE_REQUEST sending, stop-at-stop_t logic.
    Task 5 will add: MCM subscription, grant processing.
    """

    def __init__(self, station_id, vehicle_id, ramp, main_road, vanetza_session,
                 vehicle_length_m=VEHICLE_LENGTH_M):
        """Pré-calcula geometria estática (stop_t, zona de conflito) e inicializa estado do protocolo."""
        self.station_id      = station_id
        self.vehicle_id      = vehicle_id
        self.vanetza_session = vanetza_session

        L_ramp = road_length(ramp)
        self.L_ramp    = L_ramp
        self.merge_lat = ramp["end"]["lat"]
        self.merge_lon = ramp["end"]["lon"]

        # ponto paramétrico na rampa onde o MC para se não tiver grants
        self.stop_t = mc_stop_t(L_ramp, vehicle_length_m=vehicle_length_m)

        # zona de conflito na main road: vehicle_length/2 + safety_gap de cada lado do merge point
        zone_half = vehicle_length_m / 2 + SAFETY_GAP_M
        self.cz_t_start, self.cz_t_end = conflict_zone_t(
            self.merge_lat, self.merge_lon, main_road,
            before_m=zone_half, after_m=zone_half,
        )
        self.L_main = road_length(main_road)

        self._lock         = threading.Lock()
        self._manoeuvre_id = 0
        self._request_sent = False
        self._last_send_ts = 0.0

        # populado por on_merge_grant (task 5)
        self.grants_received = set()
        self.merge_decided   = False

        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{vehicle_id}] MCProtocol: stop_t={self.stop_t:.3f} "
            f"cz=[{self.cz_t_start:.4f}, {self.cz_t_end:.4f}]"
        )

    def tick(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        """Chamado a cada tick pelo simulation loop.
        Retorna {'advance': bool, 'target_speed': float|None}."""
        result = {"advance": True, "target_speed": None}

        if self.merge_decided:
            return result

        eta_s = (1.0 - t) * self.L_ramp / speed_ms if speed_ms > 0 else float("inf")

        if eta_s <= CONFLICT_HORIZON_S:
            self._maybe_send_merge_request(t, speed_ms, lat, lon, bearing,
                                           eta_s, neighbours_snapshot)

        if t >= self.stop_t and not self.merge_decided:
            result["advance"] = False

        return result

    def _maybe_send_merge_request(self, t, speed_ms, lat, lon, bearing,
                                  eta_s, neighbours_snapshot):
        """Constrói e publica um MERGE_REQUEST, com throttle por MERGE_REQUEST_RESEND_S.
        Sugere a cada vizinho 70% da sua velocidade actual como velocidade de abrandamento."""
        now = time.time()
        with self._lock:
            already_sent = self._request_sent
            last_ts      = self._last_send_ts

        if already_sent and (now - last_ts) < MERGE_REQUEST_RESEND_S:
            return

        if not neighbours_snapshot:
            return

        # sugestão de abrandamento: 70% da velocidade actual de cada vizinho (ou do próprio MC como fallback)
        conflict_vehicles = [
            (sid, max(0.0, (neighbours_snapshot[sid].get("speed_ms") or speed_ms) * 0.7))
            for sid in sorted(neighbours_snapshot)
        ]

        # janela temporal de ocupação estimada: ETA ± 2 segundos
        eta_start_ms = max(0, int((eta_s - 2.0) * 1000))
        eta_end_ms   = int((eta_s + 2.0) * 1000)

        with self._lock:
            self._manoeuvre_id += 1
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

    def on_merge_grant(self, sender_id):
        """Stub para task 5 — regista grant recebido."""
        with self._lock:
            self.grants_received.add(sender_id)
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{self.vehicle_id}] MERGE_GRANT recebido de {sender_id} "
            f"grants={self.grants_received}"
        )
