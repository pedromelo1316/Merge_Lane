# TODO — Cooperative Lane Merge

Desenvolvimento incremental. Cada etapa deve ser testável antes de avançar para a seguinte.

---

## Etapa 1 — Definir as estradas e visualizá-las

- [x] Escolher as coordenadas GPS reais da via principal e da rampa
- [x] Criar `roads.json` com os dois segmentos (start/end em lat/lon)
- [x] Criar uma página HTML que lê `roads.json` e desenha os segmentos num canvas ou SVG

**Teste:** abrir o HTML no browser e ver os dois segmentos e o ponto de merge desenhados corretamente.

---

## Etapa 2 — Veículos estáticos no canvas

- [x] Adicionar um marcador por veículo com posição fixa no início de cada segmento
- [x] Confirmar que os marcadores ficam sobre os segmentos desenhados

**Teste:** ver os marcadores no canvas, cada um no início da sua estrada.

---

## Etapa 3 — Movimento simulado local (sem Docker)

- [x] Escrever um script Python que lê `roads.json`
- [x] Implementar lógica de movimento por interpolação linear entre start e end com base na velocidade
- [x] Imprimir posições GPS no terminal a cada 100ms

**Teste:** posições a mudar no terminal, veículo chega ao fim do segmento sem erros.

---

## Etapa 4 — Movimento visível no canvas em tempo real

- [x] Adicionar um servidor WebSocket simples ao script Python da etapa 3
- [x] O canvas subscreve o WebSocket e atualiza os marcadores com as posições recebidas

**Teste:** abrir o browser e ver os veículos a mover-se sobre os segmentos em tempo real.

---

## Etapa 5  — Código individual do veículo

- [x] Extrair a lógica de movimento da etapa 3 para um ficheiro vehicle.py que gere um único veículo
- [x] O veículo lê roads.json, sabe em que estrada começa (via env var ou argumento), e avança sozinho
- [x] Corre 4 instâncias do mesmo ficheiro em terminais separados, cada uma com configuração diferente, e confirmas que cada uma se move independentemente

---

## Etapa 6 — Vanetza-NAP + CAM beaconing via Zenoh

- [x] Criar `vehicle/cam_builder.py` com função pura `build_cam(lat, lon, heading, speed_ms)`
- [x] `vehicle/vehicle.py` publica CAMs via Zenoh (`vanetza/in/cam`) e subscreve CAMs recebidos (`vanetza/out/cam`)
- [x] `vehicle/vehicle.py` aceita `VEHICLE_ID` por env var (para Docker) ou argumento CLI
- [x] Criar `run_vehicles.py` que lança os 4 veículos localmente, cada um ligado ao seu broker Zenoh
- [x] `docker-compose.yml` contém apenas os 4 containers Vanetza-NAP (Python corre localmente)

> **Decisão de arquitectura:** os containers Python foram removidos do Docker. O Python corre localmente via `run_vehicles.py`, ligando-se ao Zenoh de cada container Vanetza-NAP. Mais fácil de iterar sem rebuild.

**Teste:** `docker compose up` (Vanetza) + `python3 run_vehicles.py` (Python), ver nos logs os CAMs dos outros veículos a chegar.

---

## Etapa 7 — Dashboard recebe CAMs dos containers

- [x] O servidor WebSocket passa a subscrever os CAMs do Zenoh em vez de simular localmente
- [x] Canvas mostra os veículos com posições vindas dos containers Docker

**Teste:** browser com veículos a mover-se, posições vindas dos containers reais.

---

## Etapa 8 — Deteção do ponto de merge e cálculo de conflito

- [x] O veículo na rampa deteta quando está a X segundos do ponto de merge
- [x] Identifica quais os veículos da via principal em conflito nesse timestamp (com base nos CAMs recebidos)
- [x] Logar no terminal "detetei conflito com veículo X" sem enviar nada ainda

**Teste:** log do veículo na rampa a identificar corretamente os veículos em conflito com base nas posições reais.

---

## Etapa 9 — Módulo de builders de mensagens MCM

- [x] Criar `mcm_builder.py` com funções puras para cada tipo de mensagem:
  - `build_merge_request()`
  - `build_slowdown_request()`
  - `build_ack()`
  - `build_merge_grant()`
  - `build_execution_status()`

---

## Etapa 10 — Protocolo completo: MERGE_REQUEST → ACK → GRANT

### Etapa 10.1

- [x] Integrar os builders na lógica dos containers
- [x] Veículo na rampa envia `MERGE_REQUEST` quando deteta conflito
  
**Nota:** Para já veiculos que recebem o `MERGE_REQUEST` apenas logam que receberam, sem reagir.

### Etapa 10.2

- [x] Veículos em conflito propagam `SLOWDOWN_REQUEST` para o veículo atrás (inferido pelos CAMs)
- [x] Último veículo da cadeia envia `ACK`
- [x] `ACK` propaga para a frente

### Etapa 10.3

- [ ] Veiculos que receberam MCM respodnem com `MERGE_GRANT`
- [ ] Veículo na rampa valida e executa o merge (ou fallback se timeout ou negações)
- [ ] Logar cada passo com timestamp

**Teste:** nos logs do `docker compose up` ver a sequência completa de mensagens com timings corretos.

---

## Etapa 11 — Estado do protocolo visível no dashboard

- [ ] Bridge WebSocket passa a incluir o estado do protocolo de cada veículo (normal / em conflito / a abrandar / merge executado)
- [ ] Canvas mostra cores diferentes por estado em cada veículo
- [ ] Painel lateral com log de MCMs em tempo real

**Teste:** `docker compose up`, abrir o browser, ver o merge acontecer visualmente sobre os segmentos desenhados.
