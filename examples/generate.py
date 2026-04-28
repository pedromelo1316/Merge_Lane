import argparse
import json
from pathlib import Path
from time import sleep

import zenoh


BROKERS = {
    "rsu": "tcp/192.168.98.10:7447",
    "obu": "tcp/192.168.98.20:7447",
}
DEFAULT_HOST = "obu"
PUBLISH_INTERVAL_SEC = 1.0


def resolve_endpoint(host):
    if host in BROKERS:
        return BROKERS[host]
    if host.startswith("tcp/"):
        return host
    return f"tcp/{host}:7447"


def build_session(endpoint):
    config_json = '{"mode":"client","connect":{"endpoints":["' + endpoint + '"]}}'
    config = zenoh.Config.from_json5(config_json)
    return zenoh.open(config)


def load_cam_payload():
    cam_path = Path(__file__).resolve().parent / "in_cam.json"
    with cam_path.open("r", encoding="utf-8") as source:
        message = json.load(source)
    message["latitude"] = 0
    message["longitude"] = 0
    return json.dumps(message).encode("utf-8")


def generate(session, payload):
    session.put("vanetza/in/cam", payload)


def payload_to_bytes(payload):
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if hasattr(payload, "to_bytes"):
        return payload.to_bytes()
    return bytes(payload)


def on_sample(sample):
    message_bytes = payload_to_bytes(sample.payload)
    message = message_bytes.decode("utf-8", errors="replace")
    try:
        cam = json.loads(message)
        station_id = cam.get("stationID")
        if station_id is None:
            station_id = cam.get("fields", {}).get("header", {}).get("stationId")
        print(station_id)
    except json.JSONDecodeError:
        print("invalid_json")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate CAMs and subscribe to CAM output using a broker hostname."
    )
    parser.add_argument(
        "host",
        nargs="?",
        default=DEFAULT_HOST,
        help="Broker hostname to map (rsu|obu) or a raw host/IP.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    endpoint = resolve_endpoint(args.host)
    session = build_session(endpoint)
    session.declare_subscriber("vanetza/out/cam", on_sample)

    payload = load_cam_payload()
    while True:
        generate(session, payload)
        sleep(PUBLISH_INTERVAL_SEC)


if __name__ == "__main__":
    main()
