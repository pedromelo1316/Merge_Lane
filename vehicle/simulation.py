import json
import time

from cam_builder import build_cam
from comms import NeighbourTable, make_cam_callback
from geo import (
    DT, VEHICLE_LENGTH_M, SAFETY_GAP_M,
    advance_speed, compute_bearing, haversine, project_t, road_length,
)
from protocol import MCProtocol


def _gap_ahead(own_t, own_sid, road, snap, L):
    """Metros (frente-a-traseira) até ao veículo mais próximo à frente na mesma estrada.
    Confirma que o vizinho está nesta estrada pela distância ao ponto projetado (< 25 m),
    consistente com find_vehicle_behind em geo.py."""
    s, e  = road["start"], road["end"]
    dlat  = e["lat"] - s["lat"]
    dlon  = e["lon"] - s["lon"]
    L2    = dlat ** 2 + dlon ** 2
    best_t = float("inf")
    for sid, state in snap.items():
        if sid == own_sid:
            continue
        t_n = ((state["lat"] - s["lat"]) * dlat + (state["lon"] - s["lon"]) * dlon) / L2
        if t_n <= own_t:
            continue
        proj_lat = s["lat"] + t_n * dlat
        proj_lon = s["lon"] + t_n * dlon
        if haversine(proj_lat, proj_lon, state["lat"], state["lon"]) > 25.0:
            continue
        best_t = min(best_t, t_n)
    if best_t == float("inf"):
        return float("inf")
    return (best_t - own_t) * L - VEHICLE_LENGTH_M  # gap frente-a-traseira


def _move_loop(road, t0, speed0, target_speed, station_id,
               vanetza_session, stop_event, label,
               neighbours=None, roads=None, vehicle_length_m=VEHICLE_LENGTH_M,
               protocol=None):
    """Loop de movimento ao longo de road a partir de t0. Retorna (lat, lon) final."""
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

        # Car-following: manter gap mínimo ao veículo da frente (todos os veículos)
        if neighbours:
            snap = neighbours.snapshot()
            gap  = _gap_ahead(t, station_id, road, snap, L)
            if gap < SAFETY_GAP_M:
                effective_target = min(effective_target,
                                       speed * max(0.0, gap / SAFETY_GAP_M))

        # Tick do protocolo (apenas MC)
        if protocol:
            snap = neighbours.snapshot() if neighbours else {}
            result = protocol.tick(t, speed, lat, lon, bearing, snap)
            if result.get("target_speed") is not None:
                effective_target = result["target_speed"]
            should_advance = result.get("advance", True)

        speed, accel = advance_speed(speed, effective_target, DT)

        cam = build_cam(lat, lon, bearing, speed, lane, accel)
        vanetza_session.put("vanetza/in/cam", json.dumps(cam).encode())

        if should_advance:
            t += speed * DT / L
        time.sleep(DT)

    lat = s["lat"] + min(t, 1.0) * (e["lat"] - s["lat"])
    lon = s["lon"] + min(t, 1.0) * (e["lon"] - s["lon"])
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{label}] chegou ao fim de '{road['id']}' lat={lat:.5f} lon={lon:.5f}")
    return lat, lon


def run(scenario, vehicle_id, station_id, vanetza_session, stop_event):
    """Entry point de simulação para um veículo num cenário.

    Inicializa a NeighbourTable, subscreve CAMs, cria MCProtocol se for veículo na rampa,
    executa o loop de movimento e trata a transição automática rampa → main road."""
    roads = {r["id"]: r for r in scenario["roads"]}

    cfg = next((v for v in scenario["vehicles"] if v["station_id"] == station_id), None)
    if cfg is None:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{vehicle_id}] station_id={station_id} não encontrado no cenário")
        return

    road           = roads[cfg["road"]]
    t0             = project_t(cfg["lat"], cfg["lon"], road)
    target_speed   = road["speed_limit_kmh"] / 3.6
    vehicle_length = cfg.get("length_m", VEHICLE_LENGTH_M)

    neighbours = NeighbourTable()
    cam_sub = vanetza_session.declare_subscriber(
        "vanetza/out/cam",
        make_cam_callback(station_id, neighbours),
    )

    # MCProtocol apenas para veículo na rampa (MC); veículos na main road não têm protocolo
    protocol = None
    if road.get("type") == "ramp" and road.get("merges_into"):
        main_road = roads[road["merges_into"]]
        protocol = MCProtocol(station_id, vehicle_id, road, main_road,
                              vanetza_session, vehicle_length_m=vehicle_length)

    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{vehicle_id}] a mover em '{road['id']}' t0={t0:.3f} "
          f"target={target_speed:.1f}m/s length={vehicle_length}m")

    try:
        lat, lon = _move_loop(road, t0, target_speed, target_speed,
                              station_id, vanetza_session, stop_event, vehicle_id,
                              neighbours=neighbours, roads=roads,
                              vehicle_length_m=vehicle_length, protocol=protocol)

        # Transição automática rampa → main road sem verificação de protocolo (task 3 concluída,
        # task 5 irá bloquear aqui até grants estarem validados)
        if road.get("type") == "ramp" and road.get("merges_into") and not stop_event.is_set():
            main_road     = roads[road["merges_into"]]
            t2            = project_t(lat, lon, main_road)
            t2            = max(0.0, min(t2, 1.0))
            target_speed2 = main_road["speed_limit_kmh"] / 3.6
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] [{vehicle_id}] a transitar para '{main_road['id']}' t={t2:.3f}")
            _move_loop(main_road, t2, target_speed, target_speed2,
                       station_id, vanetza_session, stop_event, vehicle_id,
                       neighbours=neighbours, roads=roads,
                       vehicle_length_m=vehicle_length, protocol=None)
    finally:
        cam_sub.undeclare()
