import json
import time

from protocol import MergeProtocol

class DemoMergeProtocol(MergeProtocol):
    """
    Subclasse de MergeProtocol para modo demo.

    Quando um MERGE_REQUEST é enviado/recebido todos os veículos param.
    Há um sleep(demo_pause_s) antes de processar cada MCM recebido.
    O movimento retoma quando MERGE_CONFIRMED (agree ou abort) é recebido.
    """

    def __init__(self, *args, demo_pause_s=3.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._demo_paused   = False
        self._demo_pause_s  = demo_pause_s
        self._demo_resend_s = 1.0 + 4 * demo_pause_s

    # ── tick ─────────────────────────────────────────────────────────────────

    def tick(self, t, speed_ms, lat, lon, bearing, neighbours_snapshot):
        result = super().tick(t, speed_ms, lat, lon, bearing, neighbours_snapshot)
        if self._demo_paused:
            result = {"advance": False, "target_speed": None}
        return result

    # ── callback MCM com sleep + pause/resume ─────────────────────────────────

    def make_mcm_callback(self):
        parent_cb = super().make_mcm_callback()

        def demo_on_mcm(sample):
            try:
                payload      = json.loads(bytes(sample.payload).decode())
                sender_id    = payload.get("stationID") or payload.get("stationId")
                if sender_id == self.station_id:
                    return
                inner        = payload["fields"]["payload"]
                basic        = inner["basicContainer"]
                mcm_type     = basic["mcmType"]
                its_role     = basic.get("itssRole", 0)
                manoeuvre_id = basic.get("manoeuvreId", 0)

                is_merge_request   = (mcm_type == 1 and its_role == 1)
                is_merge_confirmed = (mcm_type == 2 and its_role == 1)

                # Determinar se ESTE veículo vai mesmo processar esta mensagem.
                # Só dormir 5s nesses casos — cada MCM é broadcast e a maioria é
                # ignorada pelos veículos para os quais não é endereçada.
                will_process = False
                if is_merge_request and not self._is_ramp():
                    will_process = True
                elif mcm_type == 1 and its_role == 3:            # SLOWDOWN_REQUEST
                    try:
                        vmc    = inner["mcmContainer"].get("vehicleManoeuvreContainer", {})
                        advice = vmc.get("manoeuvreAdvice", [])
                        will_process = bool(advice and advice[0].get("executantID") == self.station_id)
                    except (KeyError, IndexError):
                        pass
                elif mcm_type == 2 and its_role == 3 and manoeuvre_id >= 128:  # SLOWDOWN_GRANT
                    if not self._is_ramp():
                        with self._lock:
                            expected = self._slowdown_sent_to
                        will_process = (sender_id == expected)
                        print(f"[{time.strftime('%H:%M:%S')}] [{self.vehicle_id}] [DEMO] SLOWDOWN_GRANT received from {sender_id} ")

                # veículos da estrada param ao receber MERGE_REQUEST
                if is_merge_request:
                    self._demo_paused = True

                # MERGE_CONFIRMED: retomar ANTES do sleep para não bloquear o movimento
                if is_merge_confirmed:
                    self._demo_paused = False
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] [{self.vehicle_id}] [DEMO] MERGE_CONFIRMED — a retomar")

                if will_process:
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{ts}] [{self.vehicle_id}] [DEMO] a aguardar {self._demo_pause_s:.0f}s "
                          f"antes de processar MCM mcmType={mcm_type} itssRole={its_role}")
                    time.sleep(self._demo_pause_s)

                parent_cb(sample)

            except Exception as exc:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] [{self.vehicle_id}] DemoMergeProtocol MCM erro: {exc}")

        return demo_on_mcm

    # ── throttle 21s + pause MC após enviar MERGE_REQUEST ────────────────────

    def _maybe_send_merge_request(self, t, speed_ms, lat, lon, bearing,
                                  eta_s, neighbours_snapshot):
        now = time.time()
        with self._lock:
            already_sent = self._request_sent
            last_ts      = self._last_send_ts

        # throttle demo: 21s entre reenvios (substitui MERGE_REQUEST_RESEND_S=1.0 do pai)
        if already_sent and (now - last_ts) < self._demo_resend_s:
            return

        was_sent = already_sent
        super()._maybe_send_merge_request(t, speed_ms, lat, lon, bearing,
                                          eta_s, neighbours_snapshot)

        with self._lock:
            now_sent = self._request_sent

        # MC para após o primeiro envio do MERGE_REQUEST
        if not was_sent and now_sent:
            self._demo_paused = True
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{self.vehicle_id}] [DEMO] MERGE_REQUEST enviado — a pausar")

    # ── resume MC: success path ───────────────────────────────────────────────

    def _on_all_granted(self, lat, lon):
        self._demo_paused = False
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{self.vehicle_id}] [DEMO] todos os grants recebidos — a retomar")
        super()._on_all_granted(lat, lon)

    # ── resume MC: abort path ─────────────────────────────────────────────────

    def on_merge_grant(self, sender_id, manoeuvre_id, success):
        super().on_merge_grant(sender_id, manoeuvre_id, success)
        if not success:
            self._demo_paused = False
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{self.vehicle_id}] [DEMO] MERGE_GRANT(recusa) — a retomar para retry")
