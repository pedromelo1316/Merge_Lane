import json
import time

from cam_builder import build_cam
from comms import NeighbourTable, make_cam_callback
from geo import (
    DT, SAFETY_GAP_M,
    advance_speed, compute_bearing, gap_ahead, project_t, road_length,
)
from protocol_demo import DemoMergeProtocol


def _move_loop(road, t0, speed0, target_speed, station_id,
               vanetza_session, stop_event, label,
               neighbours=None, vehicle_length_m=None,
               protocol=None):
    """Loop de movimento ao longo de road a partir de t0. Retorna (lat, lon, speed) final."""
    s, e = road["start"], road["end"]
    L    = road_length(road)
    bearing = compute_bearing(s["lat"], s["lon"], e["lat"], e["lon"])
    lane = road.get("lane_position")

    t     = t0
    speed = speed0

    while t < 1.0 and not stop_event.is_set():
        lat = s["lat"] + t * (e["lat"] - s["lat"])
        lon = s["lon"] + t * (e["lon"] - s["lon"])

        effective_target = target_speed
        should_advance   = True

        snap = neighbours.snapshot() if neighbours else {}

        # Tick do protocolo (MC ou road vehicle)
        if protocol:
            result = protocol.tick(t, speed, lat, lon, bearing, snap)
            if result.get("target_speed") is not None:
                effective_target = result["target_speed"]
            should_advance = result.get("advance", True)

        # Car-following: manter gap minimo ao veiculo da frente
        if neighbours:
            gap = gap_ahead(t, station_id, road, snap, L, vehicle_length_m)
            if gap < SAFETY_GAP_M:
                effective_target = min(effective_target,
                                       speed * max(0.0, gap / SAFETY_GAP_M))

        speed, accel = advance_speed(speed, effective_target, DT)

        cam = build_cam(lat, lon, bearing, speed, vehicle_length_m, lane, accel)
        vanetza_session.put("vanetza/in/cam", json.dumps(cam).encode())

        if should_advance:
            t += speed * DT / L
        time.sleep(DT)

    lat = s["lat"] + min(t, 1.0) * (e["lat"] - s["lat"])
    lon = s["lon"] + min(t, 1.0) * (e["lon"] - s["lon"])
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{label}] chegou ao fim de '{road['id']}' lat={lat:.5f} lon={lon:.5f}")
    return lat, lon, speed


def run(scenario, vehicle_id, station_id, vanetza_session, stop_event):
    """Entry point de simulação (demo). Usa DemoMergeProtocol."""
    roads = {r["id"]: r for r in scenario["roads"]}

    cfg = next((v for v in scenario["vehicles"] if v["station_id"] == station_id), None)
    if cfg is None:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] station_id={station_id} não encontrado no cenário")
        return

    road           = roads[cfg["road"]]
    t0             = project_t(cfg["lat"], cfg["lon"], road)
    target_speed   = road["speed_limit_kmh"] / 3.6
    vehicle_length = cfg["length_m"]

    neighbours = NeighbourTable()
    cam_sub    = vanetza_session.declare_subscriber(
        "vanetza/out/cam",
        make_cam_callback(station_id, neighbours),
    )

    # detectar rampa que faz merge nesta estrada (para veículos na main road)
    ramp = next(
        (r for r in scenario["roads"] if r.get("merges_into") == road["id"]),
        None,
    )

    protocol = None
    mcm_sub  = None

    if road.get("type") == "ramp" and road.get("merges_into"):
        main_road = roads[road["merges_into"]]
        protocol  = DemoMergeProtocol(
            station_id, vehicle_id, road,
            ramp_road=road, main_road=main_road,
            vanetza_session=vanetza_session,
            road_speed_ms=target_speed, neighbours=neighbours,
            vehicle_length_m=vehicle_length,
        )
        mcm_sub   = vanetza_session.declare_subscriber(
            "vanetza/out/mcm", protocol.make_mcm_callback()
        )
    elif ramp is not None:
        protocol  = DemoMergeProtocol(
            station_id, vehicle_id, road,
            ramp_road=ramp, main_road=road,
            vanetza_session=vanetza_session,
            road_speed_ms=target_speed, neighbours=neighbours,
            vehicle_length_m=vehicle_length,
        )
        mcm_sub   = vanetza_session.declare_subscriber(
            "vanetza/out/mcm", protocol.make_mcm_callback()
        )

    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{vehicle_id}] [DEMO] a mover em '{road['id']}' t0={t0:.3f} "
          f"target={target_speed:.1f}m/s length={vehicle_length}m")

    try:
        lat, lon, speed_end = _move_loop(road, t0, target_speed, target_speed,
                         station_id, vanetza_session, stop_event, vehicle_id,
                         neighbours=neighbours,
                         vehicle_length_m=vehicle_length, protocol=protocol)

        # transição rampa → main road; só avança se merge foi decidido
        if road.get("type") == "ramp" and road.get("merges_into") and not stop_event.is_set():
            if not protocol or not protocol.merge_decided:
                return
            main_road     = roads[road["merges_into"]]
            t2            = project_t(lat, lon, main_road)
            t2            = max(0.0, min(t2, 1.0))
            target_speed2 = main_road["speed_limit_kmh"] / 3.6
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{vehicle_id}] a transitar para '{main_road['id']}' t={t2:.3f}")
            protocol.on_merge_completed(lat, lon)  # → envia EXECUTION_STATUS
            protocol.set_current_road(main_road, target_speed2)
            _move_loop(main_road, t2, speed_end, target_speed2,
                       station_id, vanetza_session, stop_event, vehicle_id,
                       neighbours=neighbours,
                       vehicle_length_m=vehicle_length, protocol=protocol)
    finally:
        cam_sub.undeclare()
        if mcm_sub:
            mcm_sub.undeclare()
