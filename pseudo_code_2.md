# Pseudo-código 2 — Routing MCM até fim do merge (passos 11–17)

```
PROCEDURE on_mcm(mensagem):
    IF mensagem.stationID == próprio: RETURN

    Extrair mcmType, itssRole, manoeuvre_id

    IF mcmType=1, itssRole=1:          // MERGE_REQUEST (MC → estrada)
        IF MC: RETURN
        on_merge_request(sender_id, mensagem)

    IF mcmType=1, itssRole=3:          // SLOWDOWN_REQUEST (veículo → veículo)
        IF executantID ≠ próprio: RETURN
        IF MC: on_ramp_slowdown_request(speed)
        ELSE:  on_slowdown_request(sender_id, speed)

    IF mcmType=2, itssRole=3, id<128:  // MERGE_GRANT (estrada → MC)
        IF MC: on_merge_grant(sender_id, success)

    IF mcmType=2, itssRole=3, id≥128:  // SLOWDOWN_GRANT (atrás → frente)
        IF road vehicle E sender esperado: on_slowdown_grant(success)

    IF mcmType=2, itssRole=1:          // MERGE_CONFIRMED (MC → estrada)
        on_merge_confirmed(success)

    IF mcmType=7, itssRole=1:          // EXECUTION_STATUS (MC → estrada)
        on_execution_status()


PROCEDURE on_merge_request(mc_id, mensagem):
    Extrair eta_mc, zona de conflito, comprimento do MC

    // Cálculo da velocidade alvo para não conflituar com o MC no ETA
    target_speed ← velocidade para sair da zona de conflito a tempo

    // Previsão da posição própria no ETA do MC
    IF veículo não estará em conflito no ETA:
        EnviarMergeGrant(success=True)
        RETURN

    pending_speed ← target_speed
    vehicle_behind ← VeículoImediatamenteAtrás()

    IF vehicle_behind existe:
        EnviarSlowdownRequest(vehicle_behind, target_speed)
    ELSE:
        IF consegue travar a tempo:
            slowed_down ← True
            EnviarMergeGrant(success=True)
        ELSE:
            EnviarMergeGrant(success=False)


PROCEDURE on_slowdown_request(sender_id, suggested_speed):
    own_speed      ← suggested_speed
    vehicle_behind ← VeículoImediatamenteAtrás()

    IF vehicle_behind existe:
        EnviarSlowdownRequest(vehicle_behind, own_speed)
    ELSE:                               // último da fila
        IF consegue travar a tempo:
            slowed_down ← True
            EnviarSlowdownGrant(sender_id, success=True)
        ELSE:
            EnviarSlowdownGrant(sender_id, success=False)


PROCEDURE on_slowdown_grant(success):
    IF NOT success:
        Cancelar pending_speed
        IF há sender à frente: EnviarSlowdownGrant(frente, False)
        ELSE:                  EnviarMergeGrant(False)
        RETURN

    IF consegue travar a tempo:
        slowed_down ← True
        IF há sender à frente: EnviarSlowdownGrant(frente, True)
        ELSE:                  EnviarMergeGrant(True)
    ELSE:
        Cancelar pending_speed
        IF há sender à frente: EnviarSlowdownGrant(frente, False)
        ELSE:                  EnviarMergeGrant(False)


PROCEDURE on_merge_grant(sender_id, success):
    IF NOT success:
        Limpar grants recebidos
        EnviarMergeConfirmed(abort)
        Aguardar cooldown e reiniciar negociação
        RETURN

    grants_received ← grants_received ∪ {sender_id}

    IF todos os peers activos deram grant:
        on_all_granted()


PROCEDURE on_all_granted():
    merge_decided ← True
    EnviarMergeConfirmed(agree)
    // _move_loop termina → transição para main road


PROCEDURE TransitarParaMainRoad(lat, lon):
    // Projeção da posição atual na main road
    t_main ← posição na main road correspondente a (lat, lon)
    EnviarExecutionStatus()
    ExecutarLoop(main_road, t_main, target_speed_main, protocol)


PROCEDURE on_merge_confirmed(success):
    IF success:
        slowed_down ← True       // mantém pending_speed até EXECUTION_STATUS
    ELSE:
        pending_speed ← None
        slowed_down   ← False    // cancela abrandamento


PROCEDURE on_execution_status():
    slowed_down   ← False
    pending_speed ← None
    // próximo tick: velocidade volta ao valor normal da estrada
```
