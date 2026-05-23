import time


def build_cam(lat, lon, heading, speed_ms, lane_position=None,length_m=4.5, width_m=1.8):
    """
    Constrói um payload CAM ETSI C-ITS.

    lat           : float — latitude em graus decimais
    lon           : float — longitude em graus decimais
    heading       : float — rumo em graus (0 = Norte, 90 = Este, sentido horário)
    speed_ms      : float — velocidade em m/s
    lane_position : int | None — faixa de rodagem (1 = mais à direita/única faixa,
                    até 14; None = campo omitido)

    Retorna um dict pronto para json.dumps(). Sem I/O nem side effects.
    """
    generation_delta_time = int(time.time() * 1000) % 65536

    return {
        "camParameters": {
            "basicContainer": {
                "stationType": 5,
                "referencePosition": {
                    "latitude": lat,
                    "longitude": lon,
                    "positionConfidenceEllipse": {
                        "semiMajorAxisLength": 4095,
                        "semiMinorAxisLength": 4095,
                        "semiMajorAxisOrientation": 3601,
                    },
                    "altitude": {
                        "altitudeValue": 800001,
                        "altitudeConfidence": 15,
                    },
                },
            },
            "highFrequencyContainer": {
                "basicVehicleContainerHighFrequency": {
                    "heading": {
                        "headingValue": heading,
                        "headingConfidence": 127,
                    },
                    "speed": {
                        "speedValue": speed_ms,
                        "speedConfidence": 127,
                    },
                    "driveDirection": 0,
                    "vehicleLength": {
                        "vehicleLengthValue": length_m,
                        "vehicleLengthConfidenceIndication": 4,
                    },
                    "vehicleWidth":  width_m,
                    "longitudinalAcceleration": {
                        "value": 0.0,
                        "confidence": 102,
                    },
                    "curvature": {
                        "curvatureValue": 0,
                        "curvatureConfidence": 7,
                    },
                    "curvatureCalculationMode": 2,
                    "yawRate": {
                        "yawRateValue": 0.0,
                        "yawRateConfidence": 8,
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
                        "steeringWheelAngleConfidence": 127,
                    },
                }
            },
            "lowFrequencyContainer": {
                "basicVehicleContainerLowFrequency": {
                    "vehicleRole": 0,
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
                    "pathHistory": [],
                }
            },
        },
        "generationDeltaTime": generation_delta_time,
    }
