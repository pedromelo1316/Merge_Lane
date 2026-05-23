import asyncio
import json
import math

import websockets


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


def load_states():
    with open("roads.json") as f:
        roads = {r["id"]: r for r in json.load(f)["roads"]}
    with open("vehicles.json") as f:
        vehicles = json.load(f)["vehicles"]

    DT = 0.1
    states = []
    for v in vehicles:
        road = roads[v["road"]]
        L_m = haversine(
            road["start"]["lat"], road["start"]["lon"],
            road["end"]["lat"],   road["end"]["lon"],
        )
        speed_ms = road["speed_limit_kmh"] / 3.6
        dt_t = speed_ms * DT / L_m
        t0 = project_t(v["lat"], v["lon"], road)
        states.append({"id": v["id"], "road": road, "t": t0, "dt_t": dt_t, "t0": t0})
    return states


clients = set()


async def register(ws):
    clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        clients.discard(ws)


async def broadcast(msg):
    if clients:
        await asyncio.gather(*[ws.send(msg) for ws in clients], return_exceptions=True)


async def simulate():
    states = load_states()
    elapsed = 0.0

    while True:
        all_done = all(s["t"] >= 1.0 for s in states)

        vehicle_list = []
        parts = [f"t={elapsed:6.2f}s"]
        for s in states:
            t = min(s["t"], 1.0)
            road = s["road"]
            lat = road["start"]["lat"] + t * (road["end"]["lat"] - road["start"]["lat"])
            lon = road["start"]["lon"] + t * (road["end"]["lon"] - road["start"]["lon"])
            parts.append(f"{s['id']}=({lat:.5f},{lon:.5f})")
            vehicle_list.append({"id": s["id"], "lat": lat, "lon": lon})

        print("  ".join(parts))
        await broadcast(json.dumps({"t": round(elapsed, 2), "vehicles": vehicle_list}))

        if all_done:
            print("[RESTART]")
            for s in states:
                s["t"] = s["t0"]
            elapsed = 0.0

        for s in states:
            if s["t"] < 1.0:
                s["t"] += s["dt_t"]

        elapsed += 0.1
        await asyncio.sleep(0.1)


async def main():
    print("WebSocket a ouvir em ws://localhost:8765")
    async with websockets.serve(register, "localhost", 8765):
        await simulate()


if __name__ == "__main__":
    asyncio.run(main())
