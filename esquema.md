# Esquema de chamadas (vehicle/)

Contexto de cenario: `vehicle.main` liga a Zenoh, recebe um cenario via `coordinator/scenario`, e chama `simulation.run`. O papel do veiculo depende do `road` do cenario: se `road.type == "ramp"` e existe `merges_into`, usa `MCProtocol`; se existir uma rampa que faz merge nesta `road`, usa `RoadVehicleProtocol`; caso contrario, nao ha protocolo.

## [vehicle/cam_builder.py](vehicle/cam_builder.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `build_cam(lat, lon, heading, speed_ms, vehicle_length_m, lane_position=None, accel_ms2=0.0)` | `simulation._move_loop` | Tick de simulacao (qualquer veiculo ativo) | `lat/lon` interpolados do `road`; `heading` = `bearing`; `speed_ms` = velocidade atual; `vehicle_length_m` = `cfg["length_m"]`; `lane_position` = `road.get("lane_position")`; `accel_ms2` = retorno de `advance_speed` |

## [vehicle/comms.py](vehicle/comms.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `open_zenoh_session(broker)` | `vehicle.main` e `open_zenoh_session_with_retry` | Arranque do processo (ligacao ao coordinator) | `broker` = `COORDINATOR_ZENOH_URL` env |
| `open_zenoh_session_with_retry(url, retries=15, delay=2.0)` | `vehicle.main` | Arranque do processo (ligacao ao Vanetza) | `url` = `VANETZA_ZENOH_URL` env; `retries/delay` defaults |
| `NeighbourTable.__init__()` | `simulation.run` | Inicio de cenario (cria estado de vizinhos) | sem parametros |
| `NeighbourTable.update(station_id, lat, lon, speed_ms, heading=None, length_m=None)` | callback de `make_cam_callback` | Quando chega CAM em `vanetza/out/cam` | `station_id/lat/lon/speed_ms/heading/length_m` extraidos do CAM recebido |
| `NeighbourTable.snapshot(max_age_s=0.2)` | `simulation._move_loop`, `RoadVehicleProtocol._on_merge_request`, `RoadVehicleProtocol._on_slowdown_request` | Tick de simulacao e calculos de cadeia | `max_age_s` default (ou passado quando chamado) |
| `make_cam_callback(own_station_id, table)` | `simulation.run` | Setup de subscriber CAM | `own_station_id` = station_id do veiculo; `table` = `NeighbourTable` usada no cenario |

## [vehicle/geo.py](vehicle/geo.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `haversine(lat1, lon1, lat2, lon2)` | `road_length`, `_on_same_road` | Calculo interno de distancias | pares lat/lon conforme chamada interna |
| `project_t(lat, lon, road)` | `simulation.run`, `simulation._move_loop`, `conflict_zone_t`, `RoadVehicleProtocol._on_merge_request` | Posicao inicial/pos-merge e zona de conflito | `lat/lon` de veiculo ou TRR; `road` do cenario |
| `compute_bearing(lat1, lon1, lat2, lon2)` | `simulation._move_loop` | Tick de simulacao (direcao do veiculo) | `lat/lon` do `road.start/end` |
| `road_length(road)` | `simulation._move_loop`, `MCProtocol.__init__`, `RoadVehicleProtocol.__init__`, `conflict_zone_t`, `find_vehicle_behind` | Inicio de cenario e calculos de zona | `road` do cenario |
| `advance_speed(cur, target, dt)` | `simulation._move_loop` | Tick de simulacao (dinamica) | `cur` = velocidade atual; `target` = alvo efetivo; `dt` = `DT` |
| `_on_same_road(state, s, dlat, dlon, L2, t_n)` | `find_vehicle_behind`, `gap_ahead` | Filtro interno de vizinhos | parametros internos derivados da estrada e estado vizinho |
| `find_vehicle_behind(own_t, own_station_id, road, neighbours_snapshot)` | `RoadVehicleProtocol._on_merge_request`, `RoadVehicleProtocol._on_slowdown_request` | Quando chega MERGE_REQUEST/SLOWDOWN_REQUEST | `own_t` do veiculo; `road` atual; snapshot de vizinhos |
| `gap_ahead(own_t, own_sid, road, snap, L, vehicle_length_m)` | `simulation._move_loop` | Car-following em cada tick | `own_t` e `road` do veiculo; `snap` de vizinhos; `L` da estrada; `vehicle_length_m` do veiculo |
| `conflict_zone_t(merge_lat, merge_lon, main_road, before_m, after_m)` | `MCProtocol.__init__`, `RoadVehicleProtocol.__init__`, `RoadVehicleProtocol._on_merge_request` | Calculo da zona de conflito | `merge_lat/lon` do ponto de merge; `main_road`; margens `before_m/after_m` |
| `vehicle_in_zone(t_vehicle, L_road, vehicle_length_m, t_start, t_end)` | `RoadVehicleProtocol._on_merge_request` | Decisao se veiculo entra na zona no ETA do MC | `t_vehicle` predito; `L_road`; `vehicle_length_m`; limites da zona |
| `merge_entry_margin(v_mc_ms, v_main_ms)` | `MCProtocol.__init__` | Ajuste de margem da zona (MC) | velocidades limite da rampa e estrada principal |
| `can_brake_in_time(cur_speed_ms, target_speed_ms, avail_dist_m)` | `RoadVehicleProtocol._on_merge_request`, `_on_slowdown_request`, `_on_slowdown_grant` | Validacao de travagem | velocidades atual/alvo e distancia disponivel |
| `mc_stop_t(L_ramp, vehicle_length_m)` | `MCProtocol.__init__` | Ponto onde o MC para na rampa | `L_ramp` e `vehicle_length_m` |

## [vehicle/mcm_builder.py](vehicle/mcm_builder.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `_make_basic_container(...)` | `build_merge_request`, `build_slowdown_request`, `build_ack`, `build_slowdown_grant`, `build_merge_grant`, `build_merge_confirmed`, `build_execution_status` | Construcao interna do payload MCM | argumentos das funcoes publicas |
| `_vehicle_state(speed_ms, heading, vehicle_length_m)` | `build_merge_request`, `build_slowdown_request` | Construcao interna do payload MCM | `speed_ms/heading/vehicle_length_m` das funcoes publicas |
| `_trr_description(...)` | `build_merge_request` | Construcao interna do TRR | coordenadas da zona de conflito e heading do MC |
| `_advised_submanoeuvre(suggested_speed_ms)` | `build_merge_request`, `build_slowdown_request` | Construcao interna de advice | `suggested_speed_ms` dado pela logica do protocolo |
| `build_merge_request(...)` | `MCProtocol._maybe_send_merge_request` | MC perto do merge e com peers ativos | `station_id/lat/lon/heading/speed_ms`; `manoeuvre_id`; `conflict_vehicles`; `vehicle_length_m`; `eta_start_ms/eta_end_ms`; `zone_start/end`; `zone_length_m` |
| `build_slowdown_request(...)` | `RoadVehicleProtocol._send_slowdown_request` | Propagacao em cadeia (veiculo -> veiculo atras) | `station_id/lat/lon/heading/speed_ms`; `manoeuvre_id`; `next_vehicle_id`; `suggested_speed_ms`; `vehicle_length_m` |
| `build_ack(...)` | Nao chamado no codigo atual | N/A | N/A |
| `build_slowdown_grant(station_id, lat, lon, manoeuvre_id, success=True)` | `RoadVehicleProtocol._send_slowdown_grant` | Resposta ao SLOWDOWN_REQUEST | `station_id/lat/lon`; `manoeuvre_id` gerado; `success` calculado |
| `build_merge_grant(station_id, lat, lon, manoeuvre_id, success=True)` | `RoadVehicleProtocol._send_merge_grant` | Resposta do veiculo ao MC | `station_id/lat/lon`; `manoeuvre_id` do MC; `success` calculado |
| `build_merge_confirmed(station_id, lat, lon, manoeuvre_id, success=True)` | `MCProtocol._on_all_granted`, `MCProtocol.on_merge_grant` | MC confirma acordo ou aborta | `station_id/lat/lon`; `manoeuvre_id` atual; `success` True/False |
| `build_execution_status(station_id, lat, lon, manoeuvre_id)` | `MCProtocol.on_merge_completed` | MC inicia a manobra na main road | `station_id/lat/lon`; `manoeuvre_id` atual |

## [vehicle/protocol.py](vehicle/protocol.py)

### MCProtocol

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `MCProtocol.__init__(station_id, vehicle_id, ramp, main_road, vanetza_session, vehicle_length_m)` | `simulation.run` | Veiculo na rampa (`road.type == "ramp"`) | `station_id/vehicle_id` do veiculo; `ramp/main_road` do cenario; `vanetza_session`; `vehicle_length_m` |
| `MCProtocol.tick(t, speed_ms, lat, lon, bearing, neighbours_snapshot)` | `simulation._move_loop` | Tick da rampa (MC) | `t` posicao 0..1; `speed_ms`; `lat/lon`; `bearing`; `neighbours_snapshot` |
| `MCProtocol._maybe_send_merge_request(...)` | `MCProtocol.tick` | ETA ao merge <= `CONFLICT_HORIZON_S` | `t/speed_ms/lat/lon/bearing/eta_s`; `neighbours_snapshot` |
| `MCProtocol._on_all_granted(lat, lon)` | `MCProtocol.tick` | Todos os peers concederam ou nao ha peers | `lat/lon` atuais do MC |
| `MCProtocol.on_merge_grant(sender_id, manoeuvre_id, success)` | callback de `MCProtocol.make_mcm_callback` | Rececao de MERGE_GRANT/recusa | `sender_id` do MCM; `manoeuvre_id`; `success` do response |
| `MCProtocol.on_merge_completed(lat, lon)` | `simulation.run` | Depois da transicao rampa -> main road | `lat/lon` no ponto de transicao |
| `MCProtocol.make_mcm_callback()` | `simulation.run` | Setup de subscriber MCM | sem parametros; devolve `on_mcm(sample)` |

### RoadVehicleProtocol

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `RoadVehicleProtocol.__init__(station_id, vehicle_id, road, merge_lat, merge_lon, vanetza_session, road_speed_ms, neighbours, vehicle_length_m)` | `simulation.run` | Veiculo na main road com rampa a entrar | `station_id/vehicle_id`; `road`; `merge_lat/lon` da rampa; `vanetza_session`; `road_speed_ms`; `neighbours`; `vehicle_length_m` |
| `RoadVehicleProtocol.tick(t, speed_ms, lat, lon, bearing, neighbours_snapshot)` | `simulation._move_loop` | Tick na main road (road vehicle) | `t/speed_ms/lat/lon/bearing`; `neighbours_snapshot` |
| `RoadVehicleProtocol.make_mcm_callback()` | `simulation.run` | Setup de subscriber MCM | sem parametros; devolve `on_mcm(sample)` |
| `RoadVehicleProtocol._on_merge_request(sender_id, manoeuvre_id, inner)` | callback `on_mcm` | Recebe MERGE_REQUEST (mcmType=1, itssRole=1) | `sender_id` e `manoeuvre_id` do MCM; `inner` payload MCM |
| `RoadVehicleProtocol._on_slowdown_request(sender_id, manoeuvre_id, sugg_speed)` | callback `on_mcm` | Recebe SLOWDOWN_REQUEST (mcmType=1, itssRole=3, executantID==self) | `sender_id/manoeuvre_id`; `sugg_speed` extraida do advice |
| `RoadVehicleProtocol._on_slowdown_grant(success)` | callback `on_mcm` | Recebe SLOWDOWN_GRANT (mcmType=2, itssRole=3, id>=128) | `success` calculado do response |
| `RoadVehicleProtocol._on_merge_confirmed(success)` | callback `on_mcm` | Recebe MERGE_CONFIRMED (mcmType=2, itssRole=1) | `success` do response |
| `RoadVehicleProtocol._on_execution_status()` | callback `on_mcm` | Recebe EXECUTION_STATUS (mcmType=7, itssRole=1) | sem parametros |
| `RoadVehicleProtocol._available_dist()` | `RoadVehicleProtocol._on_merge_request`, `_on_slowdown_request`, `_on_slowdown_grant` | Calculo interno de distancia ate zona | usa `self._t`, `self.cz_t_start`, `self.L_main` |
| `RoadVehicleProtocol._calculate_target_speed(mc_eta_s)` | `RoadVehicleProtocol._on_merge_request`, `_on_slowdown_request` | Calculo interno de velocidade alvo | `mc_eta_s` do MERGE_REQUEST (ou cache) |
| `RoadVehicleProtocol._extract_suggested_speed(advice, fallback_speed)` | Nao chamado no codigo atual | N/A | N/A |
| `RoadVehicleProtocol._send_merge_grant(success=True)` | `RoadVehicleProtocol._on_merge_request`, `RoadVehicleProtocol._on_slowdown_grant` | Envia MERGE_GRANT/recusa ao MC | `success` calculado; usa `self._lat/lon`, `self._manoeuvre_id` |
| `RoadVehicleProtocol._send_slowdown_request(target_id, suggested_speed_ms, manoeuvre_id)` | `RoadVehicleProtocol._on_merge_request`, `RoadVehicleProtocol._on_slowdown_request` | Propaga SLOWDOWN_REQUEST | `target_id` veiculo atras; `suggested_speed_ms`; `manoeuvre_id` |
| `RoadVehicleProtocol._send_slowdown_grant(target_id, success=True)` | `RoadVehicleProtocol._on_slowdown_request`, `RoadVehicleProtocol._on_slowdown_grant` | Responde ao veiculo a frente | `target_id` veiculo a frente; `success` calculado |

## [vehicle/simulation.py](vehicle/simulation.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `_move_loop(road, t0, speed0, target_speed, station_id, vanetza_session, stop_event, label, neighbours=None, vehicle_length_m=None, protocol=None)` | `simulation.run` | Movimento numa estrada (antes e depois do merge) | `road` e `t0` do cenario; `speed0/target_speed` da estrada; `station_id`; `vanetza_session`; `stop_event`; `label` = `vehicle_id`; `neighbours/vehicle_length_m/protocol` conforme papel |
| `run(scenario, vehicle_id, station_id, vanetza_session, stop_event)` | `vehicle.run_scenario` | Execucao de um cenario ativo | `scenario` do coordinator; ids do veiculo; `vanetza_session`; `stop_event` |

## [vehicle/vehicle.py](vehicle/vehicle.py)

| Funcao | Chamado por | Cenario | Parametros (origem) |
| --- | --- | --- | --- |
| `_publish_done(session, station_id, vehicle_id, skipped=False)` | `vehicle.main` | Fim do cenario ou skip | `session` do coordinator; `station_id/vehicle_id`; `skipped` True/False |
| `_print_scenario_config(scenario, station_id, vehicle_id)` | `vehicle.main` | Cenario recebido e ativo | `scenario` do coordinator; ids do veiculo |
| `run_scenario(scenario, vehicle_id, station_id, vanetza_session, stop_event)` | `vehicle.main` | Wrapper de execucao de cenario | repassa parametros para `simulation.run` |
| `main()` | `if __name__ == "__main__"` | Arranque do processo | usa env: `VEHICLE_ID`, `STATION_ID`, `VANETZA_ZENOH_URL`, `COORDINATOR_ZENOH_URL` |
