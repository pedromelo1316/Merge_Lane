import time


def _make_basic_container(station_id, lat, lon, mcm_type, its_role, manoeuvre_id, with_rational=False):
    container = {
        "generationDeltaTime": int(time.time() * 1000) % 65536,
        "stationID": station_id,
        "stationType": 1,
        "itssRole": its_role,
        "position": {
            "latitude": lat,
            "longitude": lon,
            "positionConfidenceEllipse": {
                "semiMajorAxisLength": 0,
                "semiMinorAxisLength": 0,
                "semiMajorAxisOrientation": 0,
            },
            "altitude": {"altitudeValue": 2, "altitudeConfidence": 1},
        },
        "mcmType": mcm_type,
        "manoeuvreId": manoeuvre_id,
        "concept": 0,
    }
    if with_rational:
        container["rational"] = {"manoeuvreCooperationCost": 0}
    return container


def _vehicle_state(speed_ms, heading):
    return {
        "vehicleSpeed": {"speedValue": speed_ms, "speedConfidence": 1},
        "vehicleHeading": {"value": heading, "confidence": 1},
        "vehicleSize": {
            "vehicleType": 0,
            "vehicleLenth": {
                "vehicleLengthValue": 45,
                "vehicleLengthConfidenceIndication": 4,
            },
            "vehicleWidth": 18,
            "vehicleHeight": 15,
        },
    }


def _advised_submanoeuvre(suggested_speed_ms):
    return {
        "submanoeuvreId": 0,
        "advisedTrajectory": {
            "wayPointType": 2,
            "wayPoints": [
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                {"pathPosition": {"deltaLatitude": 1800, "deltaLongitude": 0, "deltaAltitude": 0}},
            ],
            "speed": [
                {"speedValue": suggested_speed_ms, "speedConfidence": 1},
                {"speedValue": suggested_speed_ms, "speedConfidence": 1},
            ],
        },
    }


def build_merge_request(station_id, lat, lon, heading, speed_ms, manoeuvre_id, conflict_vehicles):
    """
    MC → road vehicles. Requests permission to merge.

    conflict_vehicles: list of (executant_id, suggested_speed_ms) — one per conflicting vehicle.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=1, its_role=1,
            manoeuvre_id=manoeuvre_id, with_rational=True,
        ),
        "mcmContainer": {
            "vehicleManoeuvreContainer": {
                "vehicleCurrentStateContainer": _vehicle_state(speed_ms, heading),
                "submaneuvres": [
                    {
                        "submanoeuvreID": 0,
                        "submanoeuvreStrategy": {"getOnHighway": None},
                        "referenceTrajectory": {
                            "wayPointType": 2,
                            "wayPoints": [
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 1800, "deltaLongitude": 500, "deltaAltitude": 0}},
                            ],
                            "speed": [
                                {"speedValue": speed_ms, "speedConfidence": 1},
                                {"speedValue": speed_ms, "speedConfidence": 1},
                            ],
                        },
                        "temporalCharateristics": {
                            "tRROccupancyStartTime": 3000,
                            "tRROccupancyEndTime": 4500,
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


def build_slowdown_request(station_id, lat, lon, heading, speed_ms, manoeuvre_id, next_vehicle_id, suggested_speed_ms):
    """
    Conflicting vehicle → next vehicle in chain. Propagates deceleration request.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=1, its_role=3,
            manoeuvre_id=manoeuvre_id, with_rational=True,
        ),
        "mcmContainer": {
            "vehicleManoeuvreContainer": {
                "vehicleCurrentStateContainer": _vehicle_state(speed_ms, heading),
                "submaneuvres": [
                    {
                        "submanoeuvreID": 0,
                        "submanoeuvreStrategy": {"stayInLane": None},
                        "referenceTrajectory": {
                            "wayPointType": 2,
                            "wayPoints": [
                                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                                {"pathPosition": {"deltaLatitude": 2400, "deltaLongitude": 0, "deltaAltitude": 0}},
                            ],
                            "speed": [
                                {"speedValue": speed_ms, "speedConfidence": 1},
                                {"speedValue": speed_ms, "speedConfidence": 1},
                            ],
                        },
                        "temporalCharateristics": {
                            "tRROccupancyStartTime": 0,
                            "tRROccupancyEndTime": 5000,
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
    Last vehicle in chain (or propagated forward). Confirms slowdown acceptance.

    acknowledged_delta_time: generationDeltaTime of the SLOWDOWN_REQUEST being acknowledged.

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


def build_merge_grant(station_id, lat, lon, manoeuvre_id):
    """
    First conflicting vehicle → MC. Grants the merge window.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=2, its_role=3,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 0}
        },
    }


def build_execution_status(station_id, lat, lon, manoeuvre_id, success=True):
    """
    MC → road vehicles. Reports the outcome of the merge manoeuvre.

    success=True → manouevreResponse=0 (executed successfully)
    success=False → manouevreResponse=1 (aborted / timed out)

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
