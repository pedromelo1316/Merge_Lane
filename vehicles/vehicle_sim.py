#!/usr/bin/env python3
"""
vehicle_sim.py — Simulação de veículo para lane merge cooperativo.

Opção A: o sim.py actualiza posições em metros, converte para GPS,
injeta em vanetza/in/cam → Vanetza encoda em CAM ETSI e envia na rede.
Os outros veículos são lidos de vanetza/out/cam.
"""

import os
import json
import time
import math
import threading
import zenoh

# ── Configuração ──────────────────────────────────────────────────────────────
VEHICLE_ID   = os.environ.get("VEHICLE_ID", "mc")
VEHICLE_ROLE = os.environ.get("VEHICLE_ROLE", "merge_car")   # merge_car | main_road
ZENOH_ROUTER = os.environ.get("ZENOH_ROUTER", "tcp/192.168.98.5:7447")
STATION_ID   = int(os.environ.get("VANETZA_STATION_ID", 10))
STATION_TYPE = int(os.environ.get("VANETZA_STATION_TYPE", 5))
MAC_ADDR     = os.environ.get("VANETZA_MAC_ADDRESS", "6e:06:e0:03:00:10")

# ── Geometria da simulação ────────────────────────────────────────────────────
# Ponto de referência GPS (corresponde a x=0, y=0 no sistema de simulação)
REF_LAT = 40.62834
REF_LON = -8.65439

# Entry point da rampa na via principal (em metros)
ENTRY_POINT_X = 100.0


# ── Conversão metros ↔ GPS ────────────────────────────────────────────────────
METERS_PER_DEG_LAT = 111320.0

def meters_per_deg_lon(lat):
    return 111320.0 * math.cos(math.radians(lat))

def xy_to_gps(x, y):
    """Converte deslocamento em metros (x=este, y=norte) para lat/lon."""
    lat = REF_LAT + (y / METERS_PER_DEG_LAT)
    lon = REF_LON + (x / meters_per_deg_lon(REF_LAT))
    return round(lat, 8), round(lon, 8)

# ── Estado do veículo ─────────────────────────────────────────────────────────
class VehicleState:
    def __init__(self):
        self.x       = float(os.environ.get("VEHICLE_INITIAL_X",       "100.0"))
        self.y       = float(os.environ.get("VEHICLE_INITIAL_Y",         "0.0"))
        self.speed   = float(os.environ.get("VEHICLE_INITIAL_SPEED",    "14.0"))
        self.heading = float(os.environ.get("VEHICLE_INITIAL_HEADING",  "90.0"))

        self.status  = "moving"         # moving | slowing | stopped | merged
        self.lock    = threading.Lock()

        # Vizinhos conhecidos: stationId → último payload vanetza/out/cam
        self.neighbours = {}

state = VehicleState()

# ── Física ────────────────────────────────────────────────────────────────────
def physics_loop():
    dt = 0.1  # segundos
    while True:
        with state.lock:
            if VEHICLE_ROLE == "main_road":
                if state.status == "moving":
                    state.x += state.speed * dt

                elif state.status == "slowing":
                    state.speed = max(state.speed - 2.0 * dt, 4.0)
                    state.x    += state.speed * dt

            elif VEHICLE_ROLE == "merge_car":
                if state.status == "moving":
                    # move ao longo da rampa (45°) em direcção ao entry point
                    state.x += state.speed * dt * math.cos(math.radians(45))
                    state.y += state.speed * dt * math.sin(math.radians(45))

                elif state.status == "merging":
                    # transição suave para a via principal
                    state.y  = min(state.y + 1.5 * dt, 0.0)
                    state.x += state.speed * dt
                    if state.y >= -0.5:
                        state.y      = 0.0
                        state.status = "merged"
                        print(f"[{VEHICLE_ID}] Merge concluído.")

                elif state.status == "merged":
                    state.x += state.speed * dt

        time.sleep(dt)

# ── Injectar posição no Vanetza ───────────────────────────────────────────────
def build_cam_payload(x, y, speed, heading):
    """
    Constrói o payload JSON para vanetza/in/cam com o schema do Vanetza-NAP.
    Apenas os campos necessários para actualizar posição/velocidade/heading.
    """
    lat, lon = xy_to_gps(x, y)

    # speed em m/s → ETSI usa 0.01 m/s por unidade (0–16382, 16383=unavailable)
    speed_etsi = min(int(speed * 100), 16382)

    # heading em graus → ETSI usa 0.1 graus por unidade (0–3600, 3601=unavailable)
    heading_etsi = min(int(heading * 10), 3600)

    return {
        "fields": {
            "header": {
                "protocolVersion": 2,
                "messageId": 2,
                "stationId": STATION_ID
            },
            "cam": {
                "generationDeltaTime": int(time.time() * 1000) % 65536,
                "camParameters": {
                    "basicContainer": {
                        "stationType": STATION_TYPE,
                        "referencePosition": {
                            "latitude":  lat,
                            "longitude": lon,
                            "positionConfidenceEllipse": {
                                "semiMajorAxisLength":    4095,
                                "semiMinorAxisLength":    4095,
                                "semiMajorAxisOrientation": 3601
                            },
                            "altitude": {
                                "altitudeValue":      0.0,
                                "altitudeConfidence": 15
                            }
                        }
                    },
                    "highFrequencyContainer": {
                        "basicVehicleContainerHighFrequency": {
                            "heading": {
                                "headingValue":      heading_etsi,
                                "headingConfidence": 1
                            },
                            "speed": {
                                "speedValue":      speed_etsi,
                                "speedConfidence": 1
                            },
                            "driveDirection": 0,
                            "vehicleLength": {
                                "vehicleLengthValue":                  45,
                                "vehicleLengthConfidenceIndication":    0
                            },
                            "vehicleWidth": 18,
                            "longitudinalAcceleration": {
                                "value":      0,
                                "confidence": 0
                            },
                            "curvature": {
                                "curvatureValue":      0,
                                "curvatureConfidence": 0
                            },
                            "curvatureCalculationMode": 0,
                            "yawRate": {
                                "yawRateValue":      0,
                                "yawRateConfidence": 0
                            }
                        }
                    }
                }
            }
        },
        "stationID":   STATION_ID,
        "stationAddr": MAC_ADDR,
        "timestamp":   time.time()
    }

def cam_injector(session):
    """
    Publica posição actualizada em vanetza/in/cam a cada 500ms.
    O Vanetza lê isto, encoda em CAM ETSI e envia na rede.
    """
    while True:
        with state.lock:
            x, y, speed, heading = state.x, state.y, state.speed, state.heading

        payload = build_cam_payload(x, y, speed, heading)
        session.put("vanetza/in/cam", json.dumps(payload))
        time.sleep(0.5)

# ── Ler CAMs dos vizinhos ─────────────────────────────────────────────────────
def handle_cam_out(sample):
    """
    Recebe CAMs de outros veículos via vanetza/out/cam.
    Extrai posição GPS, converte para metros e guarda em state.neighbours.
    """
    try:
        data = json.loads(bytes(sample.payload).decode())
    except Exception:
        return

    sid = data.get("stationID")
    if sid is None or sid == STATION_ID:
        return   # ignora próprias CAMs (vanetza já filtra, mas por segurança)

    try:
        pos = data["fields"]["cam"]["camParameters"]["basicContainer"]["referencePosition"]
        hfc = data["fields"]["cam"]["camParameters"]["highFrequencyContainer"] \
                  ["basicVehicleContainerHighFrequency"]

        lat = pos["latitude"]
        lon = pos["longitude"]

        # converter GPS → metros relativos ao REF
        nx = (lon - REF_LON) * meters_per_deg_lon(REF_LAT)
        ny = (lat - REF_LAT) * METERS_PER_DEG_LAT

        speed_etsi   = hfc["speed"]["speedValue"]
        heading_etsi = hfc["heading"]["headingValue"]

        speed_ms = speed_etsi / 100.0 if speed_etsi < 16383 else 0.0
        heading  = heading_etsi / 10.0 if heading_etsi < 3601 else 0.0

        with state.lock:
            state.neighbours[sid] = {
                "station_id": sid,
                "x":          round(nx, 2),
                "y":          round(ny, 2),
                "speed":      round(speed_ms, 2),
                "heading":    round(heading, 1),
                "ts":         data.get("timestamp", time.time()),
            }

    except (KeyError, TypeError) as e:
        print(f"[{VEHICLE_ID}] Erro a parsear CAM de {sid}: {e}")

# ── Utilitários do protocolo ──────────────────────────────────────────────────
def eta_to_entry(x, speed):
    """Segundos até ao entry point."""
    dist = abs(ENTRY_POINT_X - x)
    return dist / max(speed, 0.1)

def in_conflict(neighbour, my_eta, window=3.0):
    """True se o vizinho estará no entry point ao mesmo tempo que o MC."""
    n_eta = eta_to_entry(neighbour["x"], neighbour["speed"])
    return abs(n_eta - my_eta) < window

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{VEHICLE_ID}] A iniciar. Role={VEHICLE_ROLE}, StationID={STATION_ID}")

    conf = zenoh.Config()
    conf.insert_json5("connect/endpoints", json.dumps([ZENOH_ROUTER]))
    conf.insert_json5("scouting/multicast/enabled", "false")

    session = zenoh.open(conf)
    print(f"[{VEHICLE_ID}] Zenoh ligado.")

    # subscrever CAMs dos vizinhos
    session.declare_subscriber("vanetza/out/cam", handle_cam_out)

    # iniciar física
    threading.Thread(target=physics_loop, daemon=True).start()

    # iniciar injecção de posição no Vanetza
    threading.Thread(target=cam_injector, args=(session,), daemon=True).start()

    print(f"[{VEHICLE_ID}] A correr. Posição inicial: x={state.x}, y={state.y}")

    try:
        while True:
            time.sleep(1)
            with state.lock:
                lat, lon = xy_to_gps(state.x, state.y)
                print(f"[{VEHICLE_ID}] x={state.x:.1f}m y={state.y:.1f}m "
                      f"speed={state.speed:.1f}m/s "
                      f"gps=({lat},{lon}) "
                      f"neighbours={list(state.neighbours.keys())}")
    except KeyboardInterrupt:
        print(f"\n[{VEHICLE_ID}] A encerrar.")
        session.close()

if __name__ == "__main__":
    main()
