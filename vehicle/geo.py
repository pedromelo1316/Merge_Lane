import math

DECEL_MS2        = 4.0
ACCEL_MS2        = 2.0
DT               = 0.1   # tick in seconds
VEHICLE_LENGTH_M = 4.5   # comprimento padrão do veículo (metros)
SAFETY_GAP_M     = 10.0  # gap mínimo frente-a-traseira entre veículos (metros)


def haversine(lat1, lon1, lat2, lon2):
    """Distância em metros entre dois pontos GPS pela fórmula de haversine."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def project_t(lat, lon, road):
    """Projecção escalar de (lat, lon) no segmento road; t=0 no início, t=1 no fim (sem clamp)."""
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2 = dlat ** 2 + dlon ** 2
    return ((lat - s["lat"]) * dlat + (lon - s["lon"]) * dlon) / L2


def compute_bearing(lat1, lon1, lat2, lon2):
    """Rumo em graus do ponto 1 para o ponto 2 (referenciado a Norte, sentido horário)."""
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def road_length(road):
    """Comprimento em metros do segmento de estrada (haversine entre start e end)."""
    s, e = road["start"], road["end"]
    return haversine(s["lat"], s["lon"], e["lat"], e["lon"])


def advance_speed(cur, target, dt):
    """Actualiza a velocidade um tick em direcção a target. Retorna (nova_velocidade, aceleração_ms2)."""
    if cur > target + 0.01:
        new = max(target, cur - DECEL_MS2 * dt)
        return new, -DECEL_MS2
    elif cur < target - 0.01:
        new = min(target, cur + ACCEL_MS2 * dt)
        return new, ACCEL_MS2
    return cur, 0.0


def _on_same_road(state, s, dlat, dlon, L2, t_n):
    """True se state está nesta estrada.
    Na simulação os veículos movem-se por interpolação exacta, pelo que a distância
    entre a posição GPS e o ponto projectado na linha é ~0 m.
    Threshold de 0.05 m absorve apenas erros de vírgula flutuante."""
    proj_lat = s["lat"] + t_n * dlat
    proj_lon = s["lon"] + t_n * dlon
    return haversine(proj_lat, proj_lon, state["lat"], state["lon"]) < 0.05


def find_vehicle_behind(own_t, own_station_id, road, neighbours_snapshot):
    """Veículo imediatamente atrás de own_t na estrada.
    Retorna (station_id, state_dict) ou None se nenhum dentro de 200m."""
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2   = dlat ** 2 + dlon ** 2
    L    = road_length(road)
    best_sid, best_t = None, float("-inf")
    for sid, state in neighbours_snapshot.items():
        if sid == own_station_id:
            continue
        t_n = ((state["lat"] - s["lat"]) * dlat + (state["lon"] - s["lon"]) * dlon) / L2
        if t_n >= own_t or (own_t - t_n) * L > 200.0 or t_n <= best_t:
            continue
        if not _on_same_road(state, s, dlat, dlon, L2, t_n):
            continue
        best_t, best_sid = t_n, sid
    return (best_sid, neighbours_snapshot[best_sid]) if best_sid is not None else None


def gap_ahead(own_t, own_sid, road, snap, L):
    """Metros (frente-a-traseira) até ao veículo mais próximo à frente na mesma estrada."""
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
        if not _on_same_road(state, s, dlat, dlon, L2, t_n):
            continue
        best_t = min(best_t, t_n)
    if best_t == float("inf"):
        return float("inf")
    return (best_t - own_t) * L - VEHICLE_LENGTH_M


def conflict_zone_t(merge_lat, merge_lon, main_road, before_m, after_m):
    """(t_start, t_end) da zona de conflito em coordenadas paramétricas na main_road.
    before_m: metros atrás do merge point; after_m: metros à frente."""
    L       = road_length(main_road)
    t_merge = project_t(merge_lat, merge_lon, main_road)
    return (t_merge - before_m / L, t_merge + after_m / L)


def vehicle_in_zone(t_vehicle, L_road, vehicle_length_m, t_start, t_end):
    """True se o veículo (centrado em t_vehicle) se sobrepõe fisicamente a [t_start, t_end]."""
    half_t = (vehicle_length_m / 2) / L_road
    return t_vehicle + half_t >= t_start and t_vehicle - half_t <= t_end


def mc_stop_t(L_ramp, vehicle_length_m=VEHICLE_LENGTH_M, safety_gap_m=SAFETY_GAP_M):
    """t paramétrico na rampa onde o MC para a aguardar grants.
    Frontal do veículo fica a safety_gap_m antes do merge point (t=1.0 na rampa)."""
    return 1.0 - (safety_gap_m + vehicle_length_m / 2) / L_ramp
