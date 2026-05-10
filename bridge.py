import asyncio
import json
import time

import websockets
import zenoh

STATION_IDS = {10: "MC", 11: "A", 12: "B", 13: "C"}
ZENOH_BROKER = "tcp/192.168.98.10:7447"

vehicle_states = {}
ws_clients = set()


def _extract_ref_position(payload):
    # vanetza/out/cam wraps the CAM in fields.cam; vanetza/own/cam may use the raw structure
    try:
        return payload["fields"]["cam"]["camParameters"]["basicContainer"]["referencePosition"]
    except KeyError:
        return payload["camParameters"]["basicContainer"]["referencePosition"]


def on_cam(sample):
    try:
        payload = json.loads(bytes(sample.payload).decode())
        station_id = payload.get("stationID") or payload.get("stationId")
        if station_id not in STATION_IDS:
            return
        ref = _extract_ref_position(payload)
        name = STATION_IDS[station_id]
        vehicle_states[name] = {"id": name, "lat": ref["latitude"], "lon": ref["longitude"]}
    except Exception:
        pass


async def ws_handler(ws):
    ws_clients.add(ws)
    try:
        await ws.wait_closed()
    finally:
        ws_clients.discard(ws)


async def broadcast_loop():
    start = time.time()
    while True:
        msg = json.dumps({
            "t": round(time.time() - start, 2),
            "vehicles": list(vehicle_states.values()),
        })
        dead = set()
        for ws in ws_clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        ws_clients.difference_update(dead)
        await asyncio.sleep(0.1)


async def main():
    config_json = f'{{"mode":"client","connect":{{"endpoints":["{ZENOH_BROKER}"]}}}}'
    session = zenoh.open(zenoh.Config.from_json5(config_json))
    session.declare_subscriber("vanetza/out/cam", on_cam)
    session.declare_subscriber("vanetza/time/cam", on_cam)
    print(f"Bridge ligado a {ZENOH_BROKER}")
    print("WebSocket a ouvir em ws://localhost:8765")
    try:
        async with websockets.serve(ws_handler, "localhost", 8765):
            await broadcast_loop()
    except asyncio.CancelledError:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())
