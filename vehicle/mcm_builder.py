import time


def _make_basic_container(station_id, lat, lon, mcm_type, its_role, manoeuvre_id, with_rational=False):
    """Constrói o McmBasicContainer partilhado por todos os tipos de MCM.

    mcm_type   : int — McmType ASN.1: 1=request, 2=response, 7=executionStatus, 9=acknowledgment
    its_role   : int — McmItssRole ASN.1: 1=coordinatingItss (MC), 3=targetVehicle (conflito)
    concept=0  : agreementSeeking — quando with_rational=True adiciona manoeuvreCooperationCost=0
    lat/lon    : graus decimais; codec Vanetza multiplica por 10^7 antes de codificar
    """
    container = {
        "generationDeltaTime": time.time(),  # segundos Unix; codec multiplica por 1000 → ms
        "stationID": station_id,
        "stationType": 1,      # 1 = vehicle (McmStationType ASN.1)
        "itssRole": its_role,
        "position": {
            "latitude": lat,
            "longitude": lon,
            "positionConfidenceEllipse": {
                "semiMajorAxisLength": 0,      # 0 = desconhecido (0.01 m units)
                "semiMinorAxisLength": 0,
                "semiMajorAxisOrientation": 0,
            },
            "altitude": {"altitudeValue": 2, "altitudeConfidence": 1},  # 2 = ~0.02 m (nível do mar)
        },
        "mcmType": mcm_type,
        "manoeuvreId": manoeuvre_id,  # Identifier1B: range 0..255
        "concept": 0,  # 0 = agreementSeeking (negociação colaborativa, sem imposição)
    }
    if with_rational:
        # manoeuvreCooperationCost=0 indica custo zero para o veículo alvo cooperar
        container["rational"] = {"manoeuvreCooperationCost": 0}
    return container


def _vehicle_state(speed_ms, heading, vehicle_length_m):
    """Estado cinemático do veículo para VehicleCurrentStateContainer.

    speedValue  : m/s; codec MCM ×100 → 0.01 m/s (máx 163.83; acima falha silenciosamente)
    heading     : graus, sentido horário a partir do Norte (Wgs84Angle)
    vehicleHeight=15 → 15 × 0.1 m = 1.5 m (placeholder; codec sem escala — valor directo)
    vehicleWidth=1.8 m → codec ×10 → 18 (0.1 m units; sentinel 62 = unavailable)
    vehicle_length_m : metros; codec ×10 → 0.1 m units (sentinel 1023 = unavailable)
    vehicleLengthConfidenceIndication=4 → 4 = unavailable
    vehicleType=0 → 0 = unknown (Iso3833VehicleType)
    """
    return {
        "vehicleSpeed": {"speedValue": speed_ms, "speedConfidence": 127},  # 127 = unavailable
        "vehicleHeading": {"value": heading, "confidence": 127},           # 127 = unavailable
        "vehicleSize": {
            "vehicleType": 0,  # 0 = unknown (Iso3833VehicleType)
            "vehicleLenth": {  # typo intencional: nome exacto do campo no schema ASN.1
                "vehicleLengthValue": vehicle_length_m,         # metros; codec ×10 → 0.1 m units
                "vehicleLengthConfidenceIndication": 4,         # 4 = unavailable
            },
            "vehicleWidth": 1.8,   # metros; codec ×10 → 0.1 m units (máx 6.0; 62 = unavailable)
            "vehicleHeight": 15,   # 0.1 m units directos (sem escala pelo codec); 15 = 1.5 m
        },
    }


def _trr_description(heading, mc_lat, mc_lon,
                     zone_start_lat, zone_start_lon,
                     zone_end_lat, zone_end_lon,
                     zone_length_m):
    """Descreve o Target Road Resource (TRR) com a zona de conflito real.

    waypoints[0] = início da zona de conflito na main road (offset em graus relativos ao MC).
    waypoints[1] = fim da zona de conflito na main road.
    trrLength = comprimento total da zona em 0.1 m (inclui safety gap, conforme ASN.1).
    Offsets máximos: ±0.013 graus ≈ ±1.46 km — zona de ~100 m está dentro do limite.
    heading.confidence=1 → valor de teste (range 1..127).
    """
    return {
        "trrType": 2,
        "laneCount": 2,
        "waypoints": [
            {"pathPosition": {
                "deltaLatitude":  zone_start_lat - mc_lat,
                "deltaLongitude": zone_start_lon - mc_lon,
                "deltaAltitude": 0,
            }},
            {"pathPosition": {
                "deltaLatitude":  zone_end_lat - mc_lat,
                "deltaLongitude": zone_end_lon - mc_lon,
                "deltaAltitude": 0,
            }},
        ],
        "heading": [
            {"value": heading, "confidence": 1},
            {"value": heading, "confidence": 1},
        ],
        "trrWidth": 1,
        "trrLength": min(4095, round(zone_length_m * 10)),
    }


def _advised_submanoeuvre(suggested_speed_ms):
    """Submanobra aconselhada com trajectória e velocidade-alvo sugerida.

    wayPointType=1 → intermediateWayPoint (WayPointType ASN.1 enum: 0=start, 1=intermediate, 2=end)
    speed repetida três vezes para preencher a lista de waypoints (ASN.1 SIZE 1..10)
    suggested_speed_ms em m/s; codec ×100 → 0.01 m/s (mesmo limite de 163.83 m/s)
    """
    return {
        "submanoeuvreId": 0,
        "advisedTrajectory": {
            "wayPointType": 1,  # 1 = intermediateWayPoint
            "wayPoints": [
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
            ],
            "speed": [
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},  # 127 = unavailable
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},
            ],
        },
    }


def build_merge_request(station_id, lat, lon, heading, speed_ms, manoeuvre_id, conflict_vehicles,
                        vehicle_length_m,
                        eta_start_ms=2000, eta_end_ms=5000,
                        zone_start_lat=0.0, zone_start_lon=0.0,
                        zone_end_lat=0.0, zone_end_lon=0.0,
                        zone_length_m=0.0):
    """
    MC → veículos da estrada. Pede permissão para fazer merge (mcmType=1/request, itssRole=1/coordinatingItss).

    conflict_vehicles : list[(executant_id, suggested_speed_ms)] — um por veículo em conflito.
    eta_start_ms/end  : janela temporal (ms desde agora) em que o MC ocupa a zona de conflito.
    zone_start/end    : GPS (graus) do início e fim da zona de conflito na main road.
    zone_length_m     : comprimento total da zona em metros (inclui safety gap).

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=1, its_role=1,
            manoeuvre_id=manoeuvre_id, with_rational=True,
        ),
        "mcmContainer": {
            "vehicleManoeuvreContainer": {
                "vehicleCurrentStateContainer": _vehicle_state(speed_ms, heading, vehicle_length_m),
                "submaneuvres": [
                    {
                        "submanoeuvreID": 0,
                        "referenceTrajectory": {
                            "wayPointType": 1,
                            "wayPoints": [
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                            ],
                            "speed": [
                                {"speedValue": speed_ms, "speedConfidence": 127},
                                {"speedValue": speed_ms, "speedConfidence": 127},
                                {"speedValue": speed_ms, "speedConfidence": 127},
                            ],
                        },
                        "targetRoadResourceIContainer": _trr_description(
                            heading, lat, lon,
                            zone_start_lat, zone_start_lon,
                            zone_end_lat, zone_end_lon,
                            zone_length_m,
                        ),
                        "temporalCharateristics": {
                            "tRROccupancyStartTime": eta_start_ms,
                            "tRROccupancyEndTime": eta_end_ms,
                        },
                    }
                ],
                "manoeuvreAdvice": [
                    {
                        "executantID": exec_id,
                        "submaneuvres": [_advised_submanoeuvre(sugg_speed)],
                    }
                    for exec_id, sugg_speed in conflict_vehicles
                ],
            }
        },
    }


def build_slowdown_request(station_id, lat, lon, heading, speed_ms, manoeuvre_id, next_vehicle_id, suggested_speed_ms, vehicle_length_m):
    """
    Veículo em conflito → veículo imediatamente atrás. Propaga pedido de abrandamento na cadeia
    (mcmType=1/request, itssRole=3/targetVehicle).

    next_vehicle_id    : stationID do veículo alvo (endereçado em manoeuvreAdvice.executantID).
    suggested_speed_ms : velocidade-alvo sugerida em m/s; o receptor pode ajustar antes de reencaminhar.
    tRROccupancyStartTime/EndTime fixos (1000/8000 ms) — placeholder até tarefa 5 calcular valores reais.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=1, its_role=3,
            manoeuvre_id=manoeuvre_id, with_rational=True,
        ),
        "mcmContainer": {
            "vehicleManoeuvreContainer": {
                "vehicleCurrentStateContainer": _vehicle_state(speed_ms, heading, vehicle_length_m),
                "submaneuvres": [
                    {
                        "submanoeuvreID": 0,
                        "referenceTrajectory": {
                            "wayPointType": 1,
                            "wayPoints": [
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                            ],
                            "speed": [
                                {"speedValue": speed_ms, "speedConfidence": 127},
                                {"speedValue": speed_ms, "speedConfidence": 127},
                                {"speedValue": speed_ms, "speedConfidence": 127},
                            ],
                        },
                        "temporalCharateristics": {
                            "tRROccupancyStartTime": 1000,
                            "tRROccupancyEndTime": 8000,
                        },
                    }
                ],
                "manoeuvreAdvice": [
                    {
                        "executantID": next_vehicle_id,
                        "submaneuvres": [_advised_submanoeuvre(suggested_speed_ms)],
                    }
                ],
            }
        },
    }


def build_ack(station_id, lat, lon, manoeuvre_id, acknowledged_delta_time):
    """
    Último veículo da cadeia → propaga para a frente. Confirma aceitação do abrandamento
    (mcmType=9/acknowledgment, itssRole=3/targetVehicle).

    acknowledged_delta_time : generationDeltaTime do SLOWDOWN_REQUEST que está a ser confirmado
                              (AcknowledgmentContainer.generationDeltaTime ASN.1).
    acknowledgedType=1      : McmType.request — identifica o tipo de mensagem que está a ser ACKed.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=9, its_role=3,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "acknowledgmentContainer": {
                "acknowledgedType": 1,
                "generationDeltaTime": acknowledged_delta_time,
            }
        },
    }


def build_slowdown_grant(station_id, lat, lon, manoeuvre_id, success=True):
    """
    Veículo → veículo à frente. Confirma ou recusa o abrandamento pedido
    (mcmType=2/response, itssRole=3/targetVehicle).

    manoeuvre_id deve estar em [128, 255] para distinguir de MERGE_GRANT (que usa [0, 127]).
    success=True  → manouevreResponse=0 (accept)
    success=False → manouevreResponse=1 (decline)

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=2, its_role=3,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 0 if success else 1}
        },
    }


def build_merge_grant(station_id, lat, lon, manoeuvre_id, success=True):
    """
    Primeiro veículo em conflito → MC. Concede ou recusa a janela de merge
    (mcmType=2/response, itssRole=3/targetVehicle).

    manoeuvre_id deve estar em [0, 127] para distinguir de SLOWDOWN_GRANT (que usa [128, 255]).
    success=True  → manouevreResponse=0 (accept) — cadeia de ACKs completa, pode avançar
    success=False → manouevreResponse=1 (decline) — algum veículo da cadeia recusou

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=2, its_role=3,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 0 if success else 1}
        },
    }


def build_merge_confirmed(station_id, lat, lon, manoeuvre_id, success=True):
    """
    MC → veículos da estrada. Acordo alcançado (todos os grants validados) ou negociação abortada
    (mcmType=2/response, itssRole=1/coordinatingItss).

    success=True  → manouevreResponse=0 (agreed) — veículos da estrada devem aplicar o abrandamento
    success=False → manouevreResponse=1 (aborted) — timeout ou validação falhou; veículos ignoram

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=2, its_role=1,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 0 if success else 1}
        },
    }


def build_execution_status(station_id, lat, lon, manoeuvre_id):
    """
    MC → veículos da estrada. MC está a executar a mudança de faixa neste momento
    (mcmType=7/executionStatus, itssRole=1/coordinatingItss).

    Ao receberem este sinal, os veículos da estrada devem manter o abrandamento até o MC concluir
    a transição e enviar MERGE_CONFIRMED (success=True) a sinalizar regresso à velocidade normal.
    manouevreResponse=0 → estado "em execução" (accept semântico no ResponseContainer).

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=7, its_role=1,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 0}
        },
    }
