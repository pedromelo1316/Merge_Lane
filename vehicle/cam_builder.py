import time


def build_cam(lat, lon, heading, speed_ms, lane_position=None, accel_ms2=0.0):
    """
    Constrói um payload CAM ETSI C-ITS (TS 103 900 V2.2.1).

    Escalas aplicadas pelo codec Vanetza antes de codificar em ASN.1/UPER:
      speedValue    × 100   (m/s → 0.01 m/s; máx JSON 163.83 — acima falha silenciosamente)
      lat/lon       × 10^7  (graus → 10^-7 graus)
      accel_ms2     × 10    (m/s² → 0.1 m/s²)
    Campos de confiança sem sensor real usam sentinel 127 (unavailable).

    lat           : float — latitude em graus decimais
    lon           : float — longitude em graus decimais
    heading       : float — rumo em graus (0 = Norte, 90 = Este, sentido horário)
    speed_ms      : float — velocidade em m/s
    lane_position : int | None — faixa de rodagem (1 = mais à direita/única faixa,
                    até 14; None = campo omitido)
    accel_ms2     : float — aceleração longitudinal em m/s² (negativa = travagem,
                    positiva = aceleração, 0 = velocidade constante)

    Retorna um dict pronto para json.dumps(). Sem I/O nem side effects.
    """
    # GenerationDeltaTime ASN.1 range 0..65535 ms — contador rolante de ~65 s
    generation_delta_time = int(time.time() * 1000) % 65536

    return {
        "camParameters": {
            "basicContainer": {
                "stationType": 5,  # 5 = passengerCar (ETSI ITS CDD StationType)
                "referencePosition": {
                    "latitude": lat,   # graus decimais; codec multiplica por 10^7
                    "longitude": lon,
                    "positionConfidenceEllipse": {
                        "semiMajorAxisLength": 4095,      # 4095 = unavailable
                        "semiMinorAxisLength": 4095,      # 4095 = unavailable
                        "semiMajorAxisOrientation": 3601, # 3601 = unavailable
                    },
                    "altitude": {
                        "altitudeValue": 800001,  # 800001 = unavailable sentinel
                        "altitudeConfidence": 15, # 15 = outOfRange
                    },
                },
            },
            "highFrequencyContainer": {
                "basicVehicleContainerHighFrequency": {
                    "heading": {
                        "headingValue": heading,   # graus, sentido horário a partir do Norte
                        "headingConfidence": 127,  # 127 = unavailable
                    },
                    "speed": {
                        "speedValue": speed_ms,   # m/s; codec multiplica por 100
                        "speedConfidence": 127,   # 127 = unavailable
                    },
                    "driveDirection": 0,  # 0 = forward
                    "vehicleLength": {
                        "vehicleLengthValue": 1023,             # 1023 = unavailable sentinel
                        "vehicleLengthConfidenceIndication": 4, # 4 = unavailable
                    },
                    "vehicleWidth": 62,  # 62 = unavailable sentinel (normal: 0–61 → 0–6.1 m após ×10)
                    "longitudinalAcceleration": {
                        "value": round(accel_ms2, 2),  # m/s²; codec multiplica por 10
                        "confidence": 102,
                    },
                    "curvature": {
                        "curvatureValue": 0,      # 0 = trajectória recta
                        "curvatureConfidence": 7, # 7 = unavailable
                    },
                    "curvatureCalculationMode": 2,  # 2 = unavailable
                    "yawRate": {
                        "yawRateValue": 0.0,
                        "yawRateConfidence": 8,  # 8 = unavailable
                    },
                    **({"lanePosition": lane_position} if lane_position is not None else {}),
                    "accelerationControl": {
                        "brakePedalEngaged": False,
                        "gasPedalEngaged": False,
                        "emergencyBrakeEngaged": False,
                        "collisionWarningEngaged": False,
                        "accEngaged": False,
                        "cruiseControlEngaged": False,
                        "speedLimiterEngaged": False,
                    },
                    "steeringWheelAngle": {
                        "steeringWheelAngleValue": 0,
                        "steeringWheelAngleConfidence": 127,  # 127 = unavailable
                    },
                }
            },
            "lowFrequencyContainer": {
                "basicVehicleContainerLowFrequency": {
                    "vehicleRole": 0,  # 0 = default (veículo de passageiros normal)
                    "exteriorLights": {
                        "lowBeamHeadlightsOn": False,
                        "highBeamHeadlightsOn": False,
                        "leftTurnSignalOn": False,
                        "rightTurnSignalOn": False,
                        "daytimeRunningLightsOn": False,
                        "reverseLightOn": False,
                        "fogLightOn": False,
                        "parkingLightsOn": False,
                    },
                    "pathHistory": [],  # lista vazia = sem histórico de trajecto
                }
            },
        },
        "generationDeltaTime": generation_delta_time,
    }
