# Fusão cooperativa de via através de V2V

## Visão geral

Este projeto simula uma fusão cooperativa de vias usando comunicação V2X (Vehicle-to-Everything). Um veículo na rampa (Merge Car, MC) pede autorização para entrar na via principal, e os veículos da via principal coordenam um abrandamento controlado através de um protocolo distribuído. A troca de mensagens usa CAM e MCM (ETSI C-ITS), com transporte via Vanetza-NAP e Zenoh, e uma dashboard HTML para visualizar a simulação em tempo real.

São suportados cenários com um ou dois MCs a fundir simultaneamente.

## Tecnologias

| Camada | Tecnologia |
|---|---|
| V2X (stack ETSI C-ITS) | [Vanetza-NAP](https://github.com/nap-it/vanetza-nap) |
| Middleware pub/sub | Zenoh |
| Simulação | Python 3 |
| Containerização | Docker Compose |
| Visualização | HTML canvas (sem mapa de fundo) |

## Protocolo de merge (resumo)

1. **Beaconing contínuo** — Todos os veículos emitem CAMs a cada 100 ms com posição, velocidade, aceleração e comprimento.
2. **MERGE_REQUEST** — Quando o MC está a ~4 s do ponto de conflito, envia um `MERGE_REQUEST` com a janela temporal estimada de ocupação da zona de conflito e velocidades sugeridas para cada peer.
3. **Verificação de conflito** — Cada veículo da via principal verifica se estará na zona de conflito durante a janela do MC.
   - Se não estiver em conflito: responde imediatamente com `MERGE_GRANT(ok)`.
   - Se estiver em conflito: calcula a velocidade alvo e envia `SLOWDOWN_REQUEST` ao veículo diretamente atrás.
4. **Cadeia de SLOWDOWN** — O último veículo da cadeia verifica se consegue travar e responde com `SLOWDOWN_GRANT(ok/fail)`, que se propaga para a frente.
5. **MERGE_GRANT** — O primeiro veículo em conflito recebe o grant da cadeia e envia `MERGE_GRANT` ao MC.
6. **MERGE_CONFIRMED** — O MC recolhe grants de todos os peers ativos e envia `MERGE_CONFIRMED(agree)`. Os veículos da estrada aplicam o abrandamento. Em caso de recusa, o MC envia `MERGE_CONFIRMED(abort)` e retenta.
7. **EXECUTION_STATUS** — Após transitar para a via principal, o MC envia `EXECUTION_STATUS`; os veículos retomam a velocidade normal.
8. **Fallback** — Se o MC chegar ao fim da rampa sem grants suficientes, para e continua a retentar.

## Tipos de MCM usados

| Subtipo | mcmType | itssRole | manoeuvreId | Direção |
|---|---|---|---|---|
| `MERGE_REQUEST` | 1 | 1 | 0–127 | MC → todos |
| `SLOWDOWN_REQUEST` | 1 | 3 | igual ao request | veículo → veículo atrás |
| `SLOWDOWN_GRANT` | 2 | 3 | **128–255** | veículo → veículo à frente |
| `MERGE_GRANT` | 2 | 3 | **0–127** | cada veículo ativo → MC |
| `MERGE_CONFIRMED` | 2 | 1 | 0–127 | MC → todos |
| `EXECUTION_STATUS` | 7 | 1 | 0–127 | MC → todos |

## Estrutura do repositório

```
merge_lane/
├── run.sh              # Arranque completo: containers, bridge, HTTP, coordinator GUI, Firefox
├── docker-compose.yml  # 4 Vanetza-NAP + 4 Python vehicles + zenoh-router
├── coordinator.py      # GUI Tkinter — carrega cenários, dispara-os via Zenoh, rastreia conclusão
├── bridge.py           # Ponte Zenoh → WebSocket para o dashboard
├── dashboard.html      # Dashboard HTML canvas com veículos e eventos MCM em tempo real
├── debug_zenoh.py      # Subscriber simples para inspecionar tópicos Zenoh
├── scenarios/          # Cenários JSON (01–06)
│   ├── 01_standard.json          # MC vs A, B, C — sem colisão
│   ├── 02_colision_b.json        # MC colide com B se não houver abrandamento
│   ├── 03_colision_all.json      # MC colide com A, B e C
│   ├── 04_2mcs_standard.json     # Dois MCs na rampa, B e C na estrada
│   ├── 05_2mcs_colision_b.json   # Dois MCs, B em conflito
│   └── 06_2mcs_collision_all.json# Dois MCs, todos os veículos em conflito
├── info/              # Material de referência (não importado pelo código)
│   ├── CAM-PDU-Descriptions.asn
│   ├── MCM-PDU-Descriptions.asn
│   └── MCM_FIELD_REFERENCE.md
└── vehicle/
    ├── Dockerfile          # Imagem Python dos containers vehicle-*-py
    ├── vehicle.py          # Entry point: lê env vars, liga Zenoh, processa cenários em loop
    ├── simulation.py       # Loop de movimento, cria NeighbourTable + protocolo, transição rampa→main
    ├── protocol.py         # MergeProtocol: lógica unificada MC + veículo da estrada
    ├── protocol_demo.py    # DemoMergeProtocol: subclasse com pausas entre passos MCM
    ├── geo.py              # Geometria: haversine, project_t, zona de conflito, modelo de travagem
    ├── comms.py            # Sessões Zenoh, NeighbourTable (thread-safe), callback CAM
    ├── cam_builder.py      # Função pura para construir payload CAM ETSI
    └── mcm_builder.py      # Funções puras para construir todos os tipos de MCM
```

## Como arrancar

```bash
# Pré-requisitos: Docker, docker-compose, Python 3 com zenoh e websockets, Firefox

./run.sh
```

O script `run.sh`:
1. Para processos antigos (bridge, HTTP, coordinator)
2. Faz `docker-compose down` e `docker-compose up -d --build`
3. Aguarda os brokers Zenoh ficarem disponíveis
4. Lança `bridge.py` (Zenoh → WebSocket na porta 8765)
5. Lança um servidor HTTP simples (porta 8000) para servir `dashboard.html`
6. Abre `http://localhost:8000/dashboard.html` no Firefox
7. Lança `coordinator.py` (GUI Tkinter)

## Coordinator GUI

O `coordinator.py` é a interface de controlo da simulação:
- Lista os ficheiros `.json` de `scenarios/` e permite executar um ou todos em sequência.
- **Demo Mode**: ativa `DemoMergeProtocol` em todos os veículos, que faz uma pausa configurável antes de processar cada MCM relevante — útil para apresentações.
- **Draw Exag**: fator de exageração visual enviado ao dashboard para tornar os veículos mais visíveis.
- Mostra o estado de cada veículo (idle / waiting / done / timeout) durante a execução.

## Configuração dos containers

Cada veículo corre como um par de containers no bridge network `vanetzalan0`:
- **Vanetza-NAP** (`192.168.98.1X:7447`): stack ETSI C-ITS, codec ASN.1/UPER, Zenoh broker
- **vehicle-*-py**: lógica Python, publica em `vanetza/in/cam` e `vanetza/in/mcm`, subscreve `vanetza/out/*`

| Veículo | Station ID | IP Vanetza |
|---|---|---|
| MC | 10 | 192.168.98.10 |
| A | 11 | 192.168.98.11 |
| B | 12 | 192.168.98.12 |
| C | 13 | 192.168.98.13 |
| zenoh-router | — | 192.168.98.5:7446 |

## Detalhes de implementação

- **Tick de simulação**: 100 ms (`DT = 0.1` s em `geo.py`); CAMs enviados a cada tick.
- **NeighbourTable**: descarta entradas com mais de 200 ms (tolera 1 CAM perdido).
- **Geometria**: projeção paramétrica `t ∈ [0,1]` ao longo de segmentos de reta GPS; haversine para distâncias reais.
- **Modelo de travagem/aceleração**: ±4 m/s² (`DECEL_MS2 = ACCEL_MS2 = 4.0`); `can_brake_in_time()` verifica viabilidade cinemática.
- **Velocidade alvo do abrandamento**: calculada por busca binária (`_solve_target_speed`) para que o veículo fique fora da zona de conflito até o MC terminar a sua ocupação.
- **Distinção MERGE_GRANT vs SLOWDOWN_GRANT**: pelo intervalo do `manoeuvreId` (0–127 vs 128–255).
- **Papel do veículo**: determinado pela estrada — veículo numa `ramp` com `merges_into` é MC; na `main` é target vehicle. O mesmo código cobre ambos os papéis.
