import argparse
import json
import math
import os
import sys
import threading
import time

from cam_builder import build_cam


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


def make_cam_callback(vehicle_id, own_station_id):
    def on_cam(sample):
        try:
            payload = json.loads(bytes(sample.payload).decode())
            station_id = payload.get("stationID") or payload.get("stationId")
            if station_id == own_station_id:
                return
            ref = payload["fields"]["cam"]["camParameters"]["basicContainer"]["referencePosition"]
            lat = ref["latitude"]
            lon = ref["longitude"]
            print(f"[{vehicle_id}] CAM recebido de stationID={station_id} pos=({lat:.5f}, {lon:.5f})")
        except Exception:
            pass
    return on_cam


STATION_IDS = {"MC": 10, "A": 11, "B": 12, "C": 13}


def parse_args():
    parser = argparse.ArgumentParser(description="Vehicle simulation with optional CAM publishing.")
    parser.add_argument("id", nargs="?", default=os.environ.get("VEHICLE_ID"),
                        help="Vehicle ID (MC, A, B, C)")
    parser.add_argument("--broker", default=os.environ.get("ZENOH_BROKER"),
                        help="Zenoh broker endpoint, e.g. tcp/192.168.98.10:7447")
    return parser.parse_args()


def main():
    args = parse_args()
    vehicle_id = args.id

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

    session = None
    if args.broker:
        try:
            session = open_zenoh_session(args.broker)
            print(f"[{vehicle_id}] Zenoh ligado a {args.broker}")
            own_station_id = STATION_IDS.get(vehicle_id)
            session.declare_subscriber(
                "vanetza/out/cam",
                make_cam_callback(vehicle_id, own_station_id),
            )
        except Exception as e:
            print(f"[{vehicle_id}] Aviso: não foi possível ligar ao broker ({e})")
            session = None

    elapsed = 0.0
    while t < 1.0:
        lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
        lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])

        print(f"[{vehicle_id}] t={elapsed:6.2f}s  lat={lat:.5f}  lon={lon:.5f}  bearing={bearing:.1f}°")

        if session is not None:
            cam = build_cam(lat, lon, bearing, speed_ms, road.get("lane_position"))
            session.put("vanetza/in/cam", json.dumps(cam).encode())

        t += dt_t
        elapsed += DT
        time.sleep(DT)

    lat = road["end"]["lat"]
    lon = road["end"]["lon"]
    print(f"[{vehicle_id}] t={elapsed:6.2f}s  lat={lat:.5f}  lon={lon:.5f}  [CHEGOU]")

    if session is not None:
        session.close()


if __name__ == "__main__":
    main()
