import time


def _make_basic_container(station_id, lat, lon, mcm_type, its_role, manoeuvre_id, with_rational=False):
    container = {
        "generationDeltaTime": time.time(),
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
        "vehicleSpeed": {"speedValue": speed_ms, "speedConfidence": 127},
        "vehicleHeading": {"value": heading, "confidence": 127},
        "vehicleSize": {
            "vehicleType": 0,
            "vehicleLenth": {
                "vehicleLengthValue": 4.5,
                "vehicleLengthConfidenceIndication": 4,
            },
            "vehicleWidth": 1.8,
            "vehicleHeight": 15,
        },
    }


def _trr_description(heading):
    return {
        "trrType": 2,
        "laneCount": 2,
        "waypoints": [
            {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
            {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
            {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
        ],
        "heading": [
            {"value": heading, "confidence": 1},
            {"value": heading, "confidence": 1},
            {"value": heading, "confidence": 1},
        ],
        "trrWidth": 1,
        "trrLength": 1,
    }


def _advised_submanoeuvre(suggested_speed_ms):
    return {
        "submanoeuvreId": 0,
        "advisedTrajectory": {
            "wayPointType": 1,
            "wayPoints": [
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
                {"pathPosition": {"deltaLatitude": 0, "deltaLongitude": 0, "deltaAltitude": 0}},
            ],
            "speed": [
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},
                {"speedValue": suggested_speed_ms, "speedConfidence": 127},
            ],
        },
    }


def build_merge_request(station_id, lat, lon, heading, speed_ms, manoeuvre_id, conflict_vehicles,
                        eta_start_ms=2000, eta_end_ms=5000):
    """
    MC → road vehicles. Requests permission to merge.

    conflict_vehicles: list of (executant_id, suggested_speed_ms) — one per conflicting vehicle.
    eta_start_ms / eta_end_ms: MC's estimated arrival window at the conflict zone, in ms from now.

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
                        "targetRoadResourceIContainer": _trr_description(heading),
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


def build_slowdown_grant(station_id, lat, lon, manoeuvre_id, success=True):
    """
    Vehicle → vehicle ahead. Confirms or refuses slowdown.

    manoeuvre_id must be in [128, 255] to distinguish from MERGE_GRANT (0-127).
    success=False → manouevreResponse=1 (refuse).

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
    First conflicting vehicle → MC. Grants or refuses the merge window.

    manoeuvre_id must be in [0, 127] to distinguish from SLOWDOWN_GRANT (128-255).
    success=False → manouevreResponse=1 (chain refused).

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
    MC → road vehicles. Agreement reached (all grants validated) or aborted.

    success=True → manouevreResponse=0 (agreed)
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


def build_execution_status(station_id, lat, lon, manoeuvre_id):
    """
    MC → road vehicles. MC is actually changing lanes now.

    manouevreResponse=2 distinguishes this from MERGE_CONFIRMED (0/1).

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=2, its_role=1,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "responseContainer": {"manouevreResponse": 2}
        },
    }
