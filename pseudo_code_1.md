# Pseudo-código 1 — Arranque até primeiro MERGE_REQUEST (passos 1–9)

```
PROCEDURE VehicleMain:
    Ler variáveis de ambiente: VEHICLE_ID, STATION_ID, VANETZA_URL, COORDINATOR_URL

    session_vanetza ← LigarZenoh(VANETZA_URL, com retry automático)
    session_coord   ← LigarZenoh(COORDINATOR_URL)

    Lançar thread: LOOP EnviarHeartbeat(STATION_ID) a cada 2s

    Subscrever "coordinator/scenario" → on_scenario

    LOOP:
        scenario ← AguardarCenário()
        IF station_id NÃO está em scenario.active_vehicles:
            PublicarDone(skipped=True)
            CONTINUE
        IniciarSimulação(scenario)
        PublicarDone()


PROCEDURE on_scenario(data):
    Abortar cenário em curso
    Enfileirar novo cenário


PROCEDURE IniciarSimulação(scenario):
    road         ← estrada do veículo no cenário
    target_speed ← velocidade limite da estrada

    neighbours ← NeighbourTable()
    Subscrever "vanetza/out/cam" → AtualizarNeighbours

    IF veículo na rampa:
        Calcular geometria de merge:
            merge_point ← fim da rampa
            stop_t      ← posição de paragem do MC na rampa
            [cz_start, cz_end] ← limites da zona de conflito na main road
        protocol ← MergeProtocol(modo=MC)
        Subscrever "vanetza/out/mcm" → protocol.on_mcm
    ELSE IF rampa entra nesta estrada:
        protocol ← MergeProtocol(modo=RoadVehicle)
        Subscrever "vanetza/out/mcm" → protocol.on_mcm

    ExecutarLoop(road, t0, target_speed, protocol)

    IF rampa E merge_decided:
        Transitar para main road


PROCEDURE AtualizarNeighbours(cam_recebido):
    IF cam_recebido.stationID == próprio: RETURN
    Extrair posição, velocidade, rumo, comprimento
    Guardar entrada na tabela com timestamp


PROCEDURE ExecutarLoop(road, t0, target_speed, protocol):
    t     ← t0
    speed ← target_speed

    WHILE t < 1.0 AND não parar:
        lat, lon ← posição por interpolação linear em t

        resultado ← protocol.tick(t, speed, lat, lon)
        IF resultado.target_speed definido:
            effective_target ← resultado.target_speed

        speed ← AtualizarVelocidade(speed → effective_target)

        cam ← ConstruirCAM(lat, lon, speed, rumo, comprimento, aceleração)
        Publicar cam em "vanetza/in/cam"

        IF resultado.advance:
            t ← t + speed × DT / comprimento_estrada
        Aguardar DT


PROCEDURE protocol.tick(t, speed, lat, lon):  // MC na rampa
    // Cálculo do ETA até ao merge point
    eta_s ← tempo estimado para chegar ao fim da rampa

    peers_main ← vizinhos que estão na main road

    IF eta_s ≤ CONFLICT_HORIZON (4s):
        IF sem peers:
            on_all_granted()   // merge direto sem negociação
        ELSE:
            EnviarMergeRequest(eta_s, peers_main)

    IF todos os peers deram grant:
        on_all_granted()

    IF próximo do stop_t E sem merge_decided:
        Parar avanço, target_speed ← 0
```
