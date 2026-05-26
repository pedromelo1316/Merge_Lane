import math

DECEL_MS2        = 4.0
ACCEL_MS2        = 2.0
DT               = 0.1   # tick in seconds
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
    t = ((lat - s["lat"]) * dlat + (lon - s["lon"]) * dlon) / L2
    return t


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


def is_on_road(state, road):
    """True se state projeta para esta estrada com erro minimo."""
    s, e = road["start"], road["end"]
    dlat = e["lat"] - s["lat"]
    dlon = e["lon"] - s["lon"]
    L2   = dlat ** 2 + dlon ** 2
    t_n = ((state["lat"] - s["lat"]) * dlat + (state["lon"] - s["lon"]) * dlon) / L2
    return _on_same_road(state, s, dlat, dlon, L2, t_n)


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
    if best_sid is not None:
        return best_sid, neighbours_snapshot[best_sid]
    return None


def gap_ahead(own_t, own_sid, road, snap, L, vehicle_length_m):
    """Metros (frente-a-traseira) até ao veículo mais próximo à frente na mesma estrada."""
    s, e  = road["start"], road["end"]
    dlat  = e["lat"] - s["lat"]
    dlon  = e["lon"] - s["lon"]
    L2    = dlat ** 2 + dlon ** 2
    best_t, best_sid = float("inf"), None
    for sid, state in snap.items():
        if sid == own_sid:
            continue
        t_n = ((state["lat"] - s["lat"]) * dlat + (state["lon"] - s["lon"]) * dlon) / L2
        if t_n <= own_t:
            continue
        if not _on_same_road(state, s, dlat, dlon, L2, t_n):
            continue
        if t_n < best_t:
            best_t, best_sid = t_n, sid
    if best_t == float("inf"):
        return float("inf")
    ahead_len_m = snap[best_sid].get("length_m") or vehicle_length_m
    gap = (best_t - own_t) * L - vehicle_length_m / 2 - ahead_len_m / 2
    return gap


def conflict_zone_t(merge_lat, merge_lon, main_road, before_m, after_m):
    """(t_start, t_end) da zona de conflito em coordenadas paramétricas na main_road.
    before_m: metros atrás do merge point; after_m: metros à frente."""
    L       = road_length(main_road)
    t_merge = project_t(merge_lat, merge_lon, main_road)
    t_start = t_merge - before_m / L
    t_end   = t_merge + after_m / L
    return t_start, t_end


def vehicle_in_zone(t_vehicle, L_road, vehicle_length_m, t_start, t_end):
    """True se o veículo (centrado em t_vehicle) se sobrepõe fisicamente a [t_start, t_end]."""
    half_t = (vehicle_length_m / 2) / L_road
    in_zone = t_vehicle + half_t >= t_start and t_vehicle - half_t <= t_end
    return in_zone


def merge_entry_margin(v_mc_ms, v_main_ms):
    """Margem extra na zona de conflito pelo diferencial de velocidade à entrada.
    Distância cinemática de travagem de v_main até v_mc: (v_main²-v_mc²)/(2a)."""
    if v_main_ms <= v_mc_ms:
        return 0.0
    margin = (v_main_ms ** 2 - v_mc_ms ** 2) / (2 * DECEL_MS2)
    return margin


def can_brake_in_time(cur_speed_ms, target_speed_ms, avail_dist_m):
    """Retorna (pode_travar: bool, dist_travagem_m: float)."""
    if cur_speed_ms <= target_speed_ms:
        return True, 0.0
    d = (cur_speed_ms ** 2 - target_speed_ms ** 2) / (2 * DECEL_MS2)
    ok = d <= avail_dist_m
    return ok, d


def mc_stop_t(L_ramp, vehicle_length_m):
    """t paramétrico na rampa onde o MC para a aguardar grants.
    Frontal do veículo fica no merge point (t=1.0 na rampa)."""
    t_stop = 1.0 - vehicle_length_m / L_ramp
    return t_stop
