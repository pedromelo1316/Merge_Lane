# TODO — vehicle.py (rewrite limpo e modular)

O objectivo é ter um codebase limpo, sem código de demo ou speedup, com cada módulo a ter uma única responsabilidade bem definida. A lógica de geometria, comunicação, protocolo e simulação devem estar completamente separadas e ser independentemente compreensíveis.

A ordem das tarefas é sequencial e incremental — cada passo produz algo testável antes de avançar para o seguinte.

---

## Estrutura de ficheiros alvo

```
vehicle/
├── cam_builder.py      # existente — não tocar
├── mcm_builder.py      # existente — não tocar
├── geo.py              # geometria e cinemática
├── comms.py            # Zenoh e estado de vizinhos
├── protocol.py         # protocolo de merge
├── simulation.py       # cenários e coordenador
└── vehicle.py          # entry point e main loop
```

---

## Tarefas por ordem

### 1. Ler variáveis de ambiente e ligar ao Vanetza
- [x] Ler `VEHICLE_ID`, `STATION_ID`, `VANETZA_ZENOH_URL`, `COORDINATOR_ZENOH_URL`
- [x] Abrir sessão Zenoh para o Vanetza (com retry)

**Teste:** veículo arranca, imprime as variáveis lidas e confirma ligação ao Vanetza

---

### 2. Ligar ao Coordinator e receber o cenário
- [x] Abrir sessão Zenoh para o coordenador
- [x] Publicar beacon de prontidão periódico (permanente, a cada 2s, mesmo durante cenário)
- [x] Receber e parsear o JSON do cenário (roads, vehicles, active_vehicles)
- [x] Extrair a configuração do próprio veículo e imprimir (road, posição, speed_limit)
- [x] Publicar done quando o cenário terminar
- [x] Abortar cenário actual e começar novo se chegar cenário durante execução

**Teste:** coordenador envia cenário, veículo imprime a sua configuração (road, posição, merge point) e envia done

---

### 3. Mover o veículo e publicar/receber CAMs
- [x] Funções de geometria puras (`geo.py`): `haversine`, `project_t`, `compute_bearing`, cálculo de ETA, constantes físicas `DECEL_MS2`/`ACCEL_MS2`
- [x] Inicializar estado do veículo (posição, velocidade, bearing, target_speed)
- [x] Main loop: avançar posição tick a tick, controlo de velocidade (convergir para `target_speed`)
- [x] Publicar CAM a cada tick
- [x] Receber CAMs de vizinhos e manter `NeighbourTable` actualizada (com lock)
- [x] Ao atingir o fim da rampa, transitar automaticamente para a main road e continuar — sem verificações, mesmo que haja sobreposição

**Teste:** todos os veículos movem-se nas suas estradas, o MC muda para a main road ao chegar ao merge point, dashboard mostra tudo

---

### 4. Detetar aproximação ao merge point e enviar MERGE_REQUEST
- [x] Definir zona de conflito como intervalo linear na main road (X metros antes e Y metros depois do merge point, ao longo da via)
- [x] MC calcula ETA ao merge point a cada tick
- [x] Ao entrar no horizonte de conflito, identificar peers activos (CAMs recentes) e enviar `MERGE_REQUEST`
- [x] Definir ponto de paragem do MC na rampa (calculado à inicialização): se não houver grants, o MC pára aqui
- [x] `find_vehicle_behind` em `geo.py`: dado o próprio `t` e snapshot de vizinhos, devolver o veículo imediatamente atrás

**Teste:** MC aproxima-se, envia MERGE_REQUEST visível no dashboard/logs, para no ponto definido se não receber resposta

---

### 5. Protocolo completo de merge
- [x] Veículos da estrada recebem `MERGE_REQUEST`: verificar conflito com a zona linear, calcular velocidade alvo, responder com MERGE_GRANT se não em conflito
- [x] Veículos em conflito propagam `SLOWDOWN_REQUEST` para o veículo imediatamente atrás
- [x] Cadeia de `SLOWDOWN_GRANT` / `SLOWDOWN_REFUSE` propaga de volta para a frente
- [x] MC recolhe grants; quando os tem todos envia `MERGE_CONFIRMED` e avança
- [x] Veículos da estrada recebem `MERGE_CONFIRMED`: aplicar velocidade pendente (abrandar)
- [x] MC envia `EXECUTION_STATUS` após concluir a transição para a main road
- [x] Veículos da estrada recebem `EXECUTION_STATUS`: retomar velocidade normal
- [x] Verificação de viabilidade de travagem antes de aceitar slowdown
- [x] Retry com cooldown se algum veículo recusar

**Teste:** cenário `01_standard.json` completo — MC executa merge sem sobreposição, veículos abrandam e retomam, dashboard mostra tudo
