import json
import threading
import time
import zenoh


def open_zenoh_session(broker: str):
    """Abre uma sessão Zenoh em modo cliente ligada ao broker indicado."""
    config_json = '{"mode":"client","connect":{"endpoints":["' + broker + '"]}}'
    config = zenoh.Config.from_json5(config_json)
    return zenoh.open(config)


def open_zenoh_session_with_retry(url: str, retries: int = 15, delay: float = 2.0):
    """Tenta abrir sessão Zenoh até retries vezes, com delay entre tentativas.
    Útil durante o arranque dos containers Docker onde o broker pode ainda não estar pronto."""
    for attempt in range(retries):
        try:
            return open_zenoh_session(url)
        except Exception as e:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] Zenoh connect to {url} falhou ({e}), retry {attempt + 1}/{retries}...")
            time.sleep(delay)
    raise RuntimeError(f"Não foi possível ligar a {url} após {retries} tentativas")


class NeighbourTable:
    """Tabela thread-safe com o estado mais recente de cada vizinho, actualizada por CAMs recebidos."""

    def __init__(self):
        self._lock  = threading.Lock()
        self._table = {}

    def update(self, station_id, lat, lon, speed_ms, heading=None, length_m=None):
        """Guarda ou substitui o estado de um vizinho (chamado pelo callback de CAM)."""
        with self._lock:
            self._table[station_id] = {"lat": lat, "lon": lon, "speed_ms": speed_ms,
                                       "heading": heading, "length_m": length_m, "ts": time.time()}

    def snapshot(self, max_age_s: float = 0.2):
        """Devolve apenas entradas recebidas nos últimos max_age_s segundos.
        CAMs são enviados a cada 0.1 s; 0.2 s tolera 1 CAM perdido."""
        now = time.time()
        with self._lock:
            return {sid: s for sid, s in self._table.items()
                    if now - s["ts"] <= max_age_s}


def make_cam_callback(own_station_id, table: NeighbourTable):
    """Cria um callback Zenoh que parseia CAMs recebidos e actualiza a NeighbourTable.
    Ignora CAMs do próprio veículo e mensagens mal-formadas."""
    def on_cam(sample):
        try:
            payload = json.loads(bytes(sample.payload).decode())
            sid = payload.get("stationID") or payload.get("stationId")
            if sid == own_station_id:
                return
            cam_params = payload["fields"]["cam"]["camParameters"]
            ref = cam_params["basicContainer"]["referencePosition"]
            lat = ref["latitude"]
            lon = ref["longitude"]
            hfc = (cam_params
                   .get("highFrequencyContainer", {})
                   .get("basicVehicleContainerHighFrequency", {}))
            speed_ms = hfc.get("speed", {}).get("speedValue")
            heading  = hfc.get("heading", {}).get("headingValue")
            vl = hfc.get("vehicleLength", {}).get("vehicleLengthValue")
            length_m = vl / 10 if (vl is not None and vl != 1023) else None
            table.update(sid, lat, lon, speed_ms, heading=heading, length_m=length_m)
        except Exception:
            pass
    return on_cam
