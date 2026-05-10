import argparse
import json
import threading
from pathlib import Path
from time import sleep

import zenoh


BROKERS = {
    "mc": "tcp/192.168.98.10:7447",
    "a": "tcp/192.168.98.11:7447",
    "b": "tcp/192.168.98.12:7447",
    "c": "tcp/192.168.98.13:7447",
}

STATION_ID_TO_HOST = {
    10: "mc",
    11: "a",
    12: "b",
    13: "c",
}

_scenario_path = Path(__file__).resolve().parent / "scenario.json"
with _scenario_path.open("r", encoding="utf-8") as _f:
    VEHICLE_STATE = json.load(_f)

DEFAULT_HOST = "mc"
PUBLISH_CAM_INTERVAL_SEC = 1.0
PUBLISH_MCM_INTERVAL_SEC = 5.0

MESSAGE_ID_TO_TYPE = {
    1: "DENM",
    2: "CAM",
    3: "POIM",
    4: "SPATEM",
    5: "MAPEM",
    6: "IVIM",
    7: "RFU1",
    8: "RFU2",
    9: "SREM",
    10: "SSEM",
    11: "EVCSN",
    12: "SAEM",
    13: "RTCMEM",
    14: "CPM",
    15: "IMZM",
    16: "VAM",
    17: "DSM",
    18: "MIM",
    19: "MVM",
    20: "MCM",
}


# Resolve o endpoint Zenoh a partir de um alias conhecido ou host bruto.
def resolve_endpoint(host):
    if host in BROKERS:
        return BROKERS[host]
    if host.startswith("tcp/"):
        return host
    return f"tcp/{host}:7447"


# Cria uma sessao Zenoh configurada para o endpoint indicado.
def build_session(endpoint):
    config_json = '{"mode":"client","connect":{"endpoints":["' + endpoint + '"]}}'
    config = zenoh.Config.from_json5(config_json)
    return zenoh.open(config)


# Carrega e prepara o payload CAM a partir do JSON de exemplo.
def load_cam_payload(host):
    cam_path = Path(__file__).resolve().parent / "in_cam.json"
    with cam_path.open("r", encoding="utf-8") as source:
        message = json.load(source)
    state = VEHICLE_STATE.get(host, VEHICLE_STATE["mc"])
    ref = message["camParameters"]["basicContainer"]["referencePosition"]
    ref["latitude"] = state["lat"]
    ref["longitude"] = state["lon"]
    hfc = message["camParameters"]["highFrequencyContainer"]["basicVehicleContainerHighFrequency"]
    hfc["heading"]["headingValue"] = state["heading"]
    hfc["speed"]["speedValue"] = state["speed"]
    return json.dumps(message).encode("utf-8")


# Carrega o payload MCM a partir do JSON de exemplo.
def load_mcm_payload():
    mcm_path = Path(__file__).resolve().parent / "MERGE_REQUEST.json"
    with mcm_path.open("r", encoding="utf-8") as source:
        message = json.load(source)
    return json.dumps(message).encode("utf-8")


# Publica um payload CAM no topico de entrada.
def generate_cam(session, payload):
    session.put("vanetza/in/cam", payload)


# Publica um payload MCM no topico de entrada.
def generate_mcm(session, payload):
    session.put("vanetza/in/mcm", payload)


# Normaliza um payload para bytes.
def payload_to_bytes(payload):
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if hasattr(payload, "to_bytes"):
        return payload.to_bytes()
    return bytes(payload)


# Determina o tipo de mensagem a partir de um payload decodificado.
def resolve_message_type(payload):
    if not isinstance(payload, dict):
        return "UNKNOWN"
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        return "UNKNOWN"
    header = fields.get("header")
    if not isinstance(header, dict):
        return "UNKNOWN"
    message_id = header.get("messageId")
    if isinstance(message_id, str):
        try:
            message_id = int(message_id)
        except ValueError:
            return "UNKNOWN"
    if not isinstance(message_id, int):
        return "UNKNOWN"
    return MESSAGE_ID_TO_TYPE.get(message_id, "UNKNOWN")
    return "UNKNOWN"


# Extrai o station ID de um payload decodificado.
def resolve_station_id(payload):
    if not isinstance(payload, dict):
        return None
    for key in ("stationID", "stationId", "station_id"):
        station_id = payload.get(key)
        if station_id is not None:
            return station_id
    fields = payload.get("fields")
    if isinstance(fields, dict):
        header = fields.get("header")
        if isinstance(header, dict):
            return header.get("stationId")
    return None


# Trata amostras Zenoh recebidas dos topicos de saida do Vanetza.
def on_sample(sample):
    message_bytes = payload_to_bytes(sample.payload)
    message = message_bytes.decode("utf-8", errors="replace")
    try:
        payload = json.loads(message)
        message_type = resolve_message_type(payload)
        station_id = resolve_station_id(payload)
        hostname = STATION_ID_TO_HOST.get(station_id, "unknown")
        station_label = station_id if station_id is not None else "unknown"
        print(f"{hostname}-{station_label}-{message_type}:")
        if message_type == "CAM":
            #print(json.dumps(payload, indent=2))
            ref = payload["fields"]["cam"]["camParameters"]["basicContainer"]["referencePosition"]
            lat = ref["latitude"]
            lon = ref["longitude"]
            ref = payload["fields"]["cam"]["camParameters"]["highFrequencyContainer"]["basicVehicleContainerHighFrequency"]
            heading = ref["heading"]["headingValue"]
            speed = ref["speed"]["speedValue"]
            print(f"  position: {lat:.6f}, {lon:.6f}")
            print(f"  heading: {heading}, speed: {speed}")
    except json.JSONDecodeError:
        print("invalid_json")


# Faz o parse dos argumentos CLI para seleccionar o host do broker.
def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate CAMs and subscribe to CAM output using a broker hostname."
    )
    parser.add_argument(
        "host",
        nargs="?",
        default=DEFAULT_HOST,
        help=(
            "Broker hostname to map (mc|vehicle-a|vehicle-b|vehicle-c) "
            "or a raw host/IP."
        ),
    )
    return parser.parse_args()



# Publica periodicamente mensagens CAM ate parar.
def cam_publisher_loop(session, stop_event, host):
    while not stop_event.is_set():
        cam_payload = load_cam_payload(host)
        generate_cam(session, cam_payload)
        stop_event.wait(PUBLISH_CAM_INTERVAL_SEC)


# Publica periodicamente mensagens MCM ate parar.
def mcm_publisher_loop(session, stop_event):
    while not stop_event.is_set():
        mcm_payload = load_mcm_payload()
        generate_mcm(session, mcm_payload)
        stop_event.wait(PUBLISH_MCM_INTERVAL_SEC)


# Ponto de entrada: abre sessao, subscreve e inicia os publishers.
def main():
    args = parse_args()
    endpoint = resolve_endpoint(args.host)
    session = build_session(endpoint)
    session.declare_subscriber("vanetza/out/**", on_sample)

    stop_event = threading.Event()
    cam_thread = threading.Thread(
        target=cam_publisher_loop,
        args=(session, stop_event, args.host),
        name="cam-publisher",
        daemon=True,
    )
    cam_thread.start()

    mcm_thread = None
    print(f"Running with host '{args.host}' resolved to endpoint '{endpoint}'")
    if args.host == "mc":
        print("Merge Car detected, starting MCM publisher thread")
        mcm_thread = threading.Thread(
            target=mcm_publisher_loop,
            args=(session, stop_event),
            name="mcm-publisher",
            daemon=True,
        )
        mcm_thread.start()

    try:
        while True:
            sleep(1.0)
    except KeyboardInterrupt:
        stop_event.set()
        cam_thread.join()
        if mcm_thread is not None:
            mcm_thread.join()


if __name__ == "__main__":
    main()
