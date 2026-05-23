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

- [x] Veiculos que receberam MCM respodnem com `MERGE_GRANT`
- [x] Veículo na rampa valida e executa o merge (ou fallback se timeout ou negações)
- [x] Logar cada passo com timestamp

**Teste:** nos logs do `docker compose up` ver a sequência completa de mensagens com timings corretos.

---

## Etapa 11 — Estado do protocolo visível no dashboard

- [x] Painel lateral com log de MCMs em tempo real

**Teste:** `docker compose up`, abrir o browser, ver o merge acontecer visualmente sobre os segmentos desenhados.

---

## Etapa 12 — Testes unitários `[sem Docker]`

- [x] Testes para `cam_builder.py` e `mcm_builder.py` (funções puras, não precisam de Docker)
- [x] Testes para `detect_conflicts()` com posições GPS sintéticas

**Teste:** `pytest` corre a verde sem Docker nem Zenoh.

---

## Etapa 13 — Dashboard: velocidade e ponto de merge `[sem Docker]`

O dashboard mostra apenas posições. Faltam indicadores de velocidade e a marcação visual do ponto de merge.

- [ ] `bridge.py`: extrair `speedValue` do `highFrequencyContainer` no CAM, incluir `speed_kmh` em `vehicle_states`
- [ ] `dashboard.html`: label de velocidade (km/h) junto ao marcador de cada veículo
- [-] `dashboard.html`: desenhar ponto de merge explicitamente (ícone ou círculo pontilhado)
- [ ] `dashboard.html`: desenhar zona de conflito (círculo semitransparente de raio proporcional a `CONFLICT_ZONE_M`)
- [ ] `dashboard.html`: cor do marcador muda consoante estado — normal (branco), a abrandar (laranja), parado (vermelho)

**Teste:** browser mostra velocidades em tempo real, ponto de merge identificado no canvas, cores dos veículos mudam durante o protocolo.

---

## Etapa 14 — Cenários variados `[só JSON]`

Os cenários 02–07 foram apagados. Só existe `01_standard_merge.json` (MC vs A, B, C).
## Cenários

- [x] `01_standard_merge.json`
      Cenário base: MC entra pela rampa e negoceia com A, B e C.

- [x] `02_mc_vs_A_only.json`
      Apenas o veículo A entra em conflito com o MC.

- [x] `03_mc_vs_AB.json`
      Os veículos A e B entram em conflito com o MC.

- [x] `04_no_conflict.json`
      Os veículos da via principal passam antes do MC chegar ao merge.

- [x] `05_all_conflict_ABC.json`
      A, B e C entram em conflito e precisam de desacelerar.

- [x] `06_mc_stops.json`
      O MC não recebe grants suficientes e para antes do merge.

- [x] `07_truck_like_mc.json`
      O MC representa um veículo pesado/camião com maiores dimensões.

- [ ] `08_high_speed_merge.json`
      Merge a alta velocidade com menor tempo de reação e negociação.

- [ ] `09_dense_traffic_extra_cars.json`
      Tráfego denso na via principal com veículos adicionais próximos da zona de merge.

- [ ] `10_two_merge_cars.json`
      Dois veículos na rampa tentam entrar cooperativamente na via principal.


**Teste:** coordenador executa todos os cenários sequencialmente sem erros nem deadlocks.

---

## Etapa 15 — Redução de velocidade efetiva nos veículos `[requer Docker]`

O protocolo de mensagens está completo, mas os veículos nunca mudam de velocidade fisicamente.
O main loop em `vehicle.py` sobrescreve `vehicle_state["speed_ms"]` com a velocidade constante da estrada a cada tick, e `dt_t` é calculado uma única vez no arranque.

- [ ] Adicionar `target_speed_ms` ao `vehicle_state` (inicialmente = speed limit da estrada)
- [ ] Main loop lê `vehicle_state["target_speed_ms"]` para avançar, em vez da constante `speed_ms`
- [ ] `dt_t` recalculado a cada tick a partir da velocidade atual
- [ ] MCM callback (SLOWDOWN_REQUEST): aplicar `vehicle_state["target_speed_ms"] = sugg_speed`
- [ ] Log no terminal: `[A] velocidade reduzida para X.XX m/s`

**Teste:** nos logs, velocidade de A/B/C baixa visivelmente após receber SLOWDOWN_REQUEST.

---

## Etapa 16 — Retoma de velocidade normal após EXECUTION_STATUS `[requer Docker, depende de 15]`

Após o merge, os veículos que abrandaram devem retomar a velocidade limite da estrada.

- [ ] Ao receber EXECUTION_STATUS(OK): `vehicle_state["target_speed_ms"] = road_speed_limit`
- [ ] Adicionar flag `slowed_down` ao `protocol_state` para distinguir veículos afetados
- [ ] Log no terminal: `[A] velocidade retomada após merge executado`

**Teste:** após EXECUTION_STATUS nos logs, velocidade dos veículos da via principal volta ao normal.

---

## Etapa 17 — Estado do protocolo por veículo no dashboard `[requer Docker]` (opcional)

- [ ] Bridge infere estado do protocolo por veículo a partir dos MCMs recebidos
- [ ] Dashboard mostra badge por veículo: `NORMAL` / `SLOWING` / `WAITING_GRANT` / `MERGING`

---

## Etapa 18 — MC transita para a via principal após merge bem sucedido `[requer Docker]`

Após `merge_decided=True` e chegar ao fim da rampa (`t >= 1.0`), o MC para completamente. Deve continuar a circular na via principal a partir do ponto de merge.

- [ ] Após `merge_decided=True` e `t >= 1.0`, iniciar segundo loop de movimento na `main_road`
- [ ] Posição inicial na main road = projeção do ponto de merge (end da rampa)
- [ ] Publicar CAMs continuamente a partir da nova posição na via principal
- [ ] Log: `[MC] a transitar para via principal em t=X.XX`

**Teste:** MC aparece no dashboard a continuar para a direita (via principal) após o merge.
