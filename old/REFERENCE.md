# vehicle_old.py — Mapa de referência

Ficheiro: `old/vehicle_old.py` (1031 linhas)

---

## Funções utilitárias (topo do ficheiro)

| Linha | Nome | O que faz |
|---|---|---|
| 15 | `haversine(lat1,lon1,lat2,lon2)` | Distância em metros entre dois pontos GPS |
| 24 | `project_t(lat,lon,road)` | Projecção de um ponto num segmento de estrada → `t ∈ [0,1]` |
| 32 | `compute_bearing(lat1,lon1,lat2,lon2)` | Rumo em graus entre dois pontos GPS |
| 40 | `open_zenoh_session(broker)` | Abre sessão Zenoh dado um URL |
| 47 | `open_zenoh_session_with_retry(url, retries=15, delay=2.0)` | Idem com retry automático |

---

## CAM

| Linha | Nome | O que faz |
|---|---|---|
| 58 | `make_cam_callback(...)` | Devolve o callback que parseia CAMs recebidos e actualiza `neighbour_states` |

O callback interno `on_cam` (linha 59) filtra o próprio `station_id`, extrai lat/lon/speed e guarda em `neighbour_states[station_id]`.

---

## MCM — callback principal

| Linha | Nome | O que faz |
|---|---|---|
| 111 | `make_mcm_callback(...)` | Devolve o callback `on_mcm` com toda a lógica de protocolo |

### Funções internas de `make_mcm_callback` (closures)

| Linha | Nome | O que faz |
|---|---|---|
| 119 | `_demo_pause()` | Pausa em modo demo — ignorar no rewrite |
| 127 | `_suggested_speed_for(target_id, mc_advice)` | Extrai velocidade sugerida do `manoeuvreAdvice` do MC para um ID específico |
| 138 | `_calculate_target_speed(mc_eta_s, lat, lon)` | Calcula a velocidade que leva o veículo à borda da zona de conflito exatamente no ETA do MC |
| 149 | `_send_slowdown(target_id, mc_advice)` | Constrói e envia `SLOWDOWN_REQUEST` para o veículo atrás |
| 185 | `_send_slowdown_grant(to_id, success=True)` | Envia `SLOWDOWN_GRANT` ou `SLOWDOWN_REFUSE` |
| 202 | `_available_dist()` | Distância disponível para travar (dist ao merge point menos buffer) |
| 209 | `_send_merge_grant(success=True)` | Envia `MERGE_GRANT` ou `MERGE_GRANT(recusa)` ao MC |

### Dispatcher `on_mcm` (linha 232) — identificação de mensagens

| Linhas | Condição | Mensagem tratada |
|---|---|---|
| 251–313 | `mcmType==2, itssRole==3, manoeuvre_id≥128` | **SLOWDOWN_GRANT** ou **SLOWDOWN_REFUSE** recebido do veículo atrás |
| 314–346 | `mcmType==2, itssRole==3, manoeuvre_id<128` | **MERGE_GRANT** recebido por um veículo da estrada (só o MC processa) |
| 349–359 | `mcmType==7, itssRole==1` | **EXECUTION_STATUS** do MC → veículos retomam velocidade normal |
| 362–388 | `mcmType==2, itssRole==1` | **MERGE_CONFIRMED** do MC → aplicar abrandamento pendente (response=0) ou abortar (response=1) |
| 394–487 | `mcmType==1, itssRole==1` | **MERGE_REQUEST** do MC → verificar conflito, propagar ou responder |
| 490–563 | `mcmType==1, itssRole==3` | **SLOWDOWN_REQUEST** de veículo à frente → filtrar por `executantID`, propagar ou responder |

---

## Envio do MERGE_REQUEST (MC)

| Linha | Nome | O que faz |
|---|---|---|
| 574 | `send_merge_request(...)` | Calcula ETA, constrói e envia `MERGE_REQUEST`; marca `request_sent=True` |

ETA calculado na linha 601: `eta_s = (1.0 - t) * L_ramp / speed_ms`  
Janela temporal: `[eta_s - 2.0s, eta_s + 2.0s]` em ms (linhas 602–603)

---

## Constantes físicas e de protocolo

| Linha | Constante | Valor | Significado |
|---|---|---|---|
| 627 | `CONFLICT_ZONE_M` | 50.0 m | Raio do círculo de conflito (a substituir por zona linear) |
| 628 | `CONFLICT_HORIZON_S` | 4.0 s | ETA abaixo do qual o MC inicia a negociação |
| 629 | `GRANT_TIMEOUT_S` | 5.0 s | Definido mas nunca usado |
| 630 | `DECELERATION_MS2` | 4 m/s² | Travagem confortável |
| 631 | `ACCELERATION_MS2` | 2 m/s² | Retoma de velocidade |

---

## Viabilidade de travagem

| Linha | Nome | O que faz |
|---|---|---|
| 634 | `can_brake_in_time(cur_speed, target_speed, avail_dist)` | `d = (v0²−v1²)/(2×4)` → devolve `(bool, distância_necessária)` |

---

## Função principal do cenário

| Linha | Nome | O que faz |
|---|---|---|
| 642 | `run_scenario(scenario, vehicle_id, own_station_id, vanetza_session)` | Inicializa tudo e corre o cenário |

### Dentro de `run_scenario`

| Linhas | O que acontece |
|---|---|
| 643–696 | Parsear cenário: roads, vehicle cfg, merge point, L_main |
| 697–713 | Inicializar `neighbour_states`, `manoeuvre_state`, `vehicle_state` |
| 715–737 | Inicializar `protocol_state` (todos os flags a False/None) |
| 744–758 | Subscrever `vanetza/out/cam` e `vanetza/out/mcm` |
| 762–879 | **Main loop** (`while t < 1.0`) |
| 770–779 | Controlo de velocidade (convergir para `target_speed`) |
| 782–783 | Publicar CAM |
| 787–869 | Lógica do MC: horizonte de conflito, envio de request, aguardar grants, parar |
| 875 | `time.sleep(DT / speed_factor)` — `DT = 0.1 s` |
| 881–938 | Pós-loop: transição para main road, `EXECUTION_STATUS`, chegada ao fim |

### `protocol_state` — campos e onde são usados

| Campo | Inicializado | Escrito | Lido |
|---|---|---|---|
| `in_conflict` | L.718 | L.430 (MERGE_REQUEST), L.370 (MERGE_CONFIRMED) | L.409 |
| `mc_station_id` | L.719 | L.431 | L.214, L.529 |
| `manoeuvre_id` | L.720 | L.432, L.517 | L.167, L.215 |
| `mc_advice` | L.721 | L.433 | L.280, L.504 |
| `mc_eta_s` | L.722 | L.434 | L.152 |
| `own_target_speed_ms` | L.723 | L.435, L.521–525 | L.163, L.281, L.463 |
| `slowdown_received` | L.724 | L.438, L.514 | L.267, L.278, L.295 |
| `slowdown_sender_id` | L.725 | L.439, L.515 | L.267, L.279 |
| `slowdown_sent_to` | L.727 | L.441, L.180 | L.258 |
| `slowdown_grant_received` | L.728 | L.443, L.277 | — |
| `merge_grant_sent` | L.730 | L.444, L.228 | L.212 |
| `grants_received` | L.731 | L.345, L.814 | L.838 |
| `merge_decided` | L.732 | L.852 | L.791, L.889 |
| `slowed_down` | L.733 | L.374, L.357 | L.354 |
| `pending_slowdown_speed_ms` | L.735 | L.293, L.371–375 | L.371 |

---

## Entry point

| Linha | O que faz |
|---|---|
| 941 | `main()` — lê env vars, abre sessões Zenoh, ciclo de cenários |
| 967 | `on_scenario(sample)` — callback do coordenador |
| 992 | `ready_loop()` — publica beacon a cada 2.0 s |
| 998 | Loop externo: espera cenário → `run_scenario` → publica done → repete |
