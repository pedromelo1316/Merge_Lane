# TODO — Cooperative Lane Merge

Desenvolvimento incremental. Cada etapa deve ser testável antes de avançar para a seguinte.

---

## Etapa 1 — Definir as estradas e visualizá-las

- [ ] Escolher as coordenadas GPS reais da via principal e da rampa
- [x] Criar `roads.json` com os dois segmentos (start/end em lat/lon)
- [x] Criar uma página HTML que lê `roads.json` e desenha os segmentos num canvas ou SVG

**Teste:** abrir o HTML no browser e ver os dois segmentos e o ponto de merge desenhados corretamente.

---

## Etapa 2 — Veículos estáticos no canvas

- [ ] Adicionar um marcador por veículo com posição fixa no início de cada segmento
- [ ] Confirmar que os marcadores ficam sobre os segmentos desenhados

**Teste:** ver os marcadores no canvas, cada um no início da sua estrada.

---

## Etapa 3 — Movimento simulado local (sem Docker)

- [ ] Escrever um script Python que lê `roads.json`
- [ ] Implementar lógica de movimento por interpolação linear entre start e end com base na velocidade
- [ ] Imprimir posições GPS no terminal a cada 100ms

**Teste:** posições a mudar no terminal, veículo chega ao fim do segmento sem erros.

---

## Etapa 4 — Movimento visível no canvas em tempo real

- [ ] Adicionar um servidor WebSocket simples ao script Python da etapa 3
- [ ] O canvas subscreve o WebSocket e atualiza os marcadores com as posições recebidas

**Teste:** abrir o browser e ver os veículos a mover-se sobre os segmentos em tempo real.

---

## Etapa 5 — Docker com Vanetza-NAP e CAM beaconing

- [ ] Criar `Dockerfile.vehicle` para o container Python
- [ ] Atualizar `docker-compose.yml` com os containers Python por veículo
- [ ] Cada container Python publica CAMs via Zenoh com a posição simulada
- [ ] Confirmar que os CAMs chegam entre containers

**Teste:** `docker compose up`, ver nos logs de cada container os CAMs dos outros veículos a chegar.

---

## Etapa 6 — Dashboard recebe CAMs dos containers

- [ ] O servidor WebSocket passa a subscrever os CAMs do Zenoh em vez de simular localmente
- [ ] Canvas mostra os veículos com posições vindas dos containers Docker

**Teste:** browser com veículos a mover-se, posições vindas dos containers reais.

---

## Etapa 7 — Deteção do ponto de merge e cálculo de conflito

- [ ] Cada veículo calcula continuamente a distância ao ponto de merge do seu segmento
- [ ] O veículo na rampa deteta quando está a X segundos do ponto de merge
- [ ] Identifica quais os veículos da via principal em conflito nesse timestamp (com base nos CAMs recebidos)
- [ ] Logar no terminal "detetei conflito com veículo X" sem enviar nada ainda

**Teste:** log do veículo na rampa a identificar corretamente os veículos em conflito com base nas posições reais.

---

## Etapa 8 — Módulo de builders de mensagens MCM

- [ ] Criar `mcm_builder.py` com funções puras para cada tipo de mensagem:
  - `build_cam()`
  - `build_merge_request()`
  - `build_slowdown_request()`
  - `build_ack()`
  - `build_merge_grant()`
  - `build_execution_status()`
- [ ] Escrever `test_builders.py` que chama cada função e imprime o JSON resultante

**Teste:** `python test_builders.py` imprime todos os JSONs válidos sem erros.

---

## Etapa 9 — Protocolo completo: MERGE_REQUEST → ACK → GRANT

- [ ] Integrar os builders na lógica dos containers
- [ ] Veículo na rampa envia `MERGE_REQUEST` quando deteta conflito
- [ ] Veículos em conflito propagam `SLOWDOWN_REQUEST` para o veículo atrás (inferido pelos CAMs)
- [ ] Último veículo da cadeia envia `ACK`
- [ ] `ACK` propaga para a frente
- [ ] Primeiro veículo conflituoso envia `MERGE_GRANT`
- [ ] Veículo na rampa valida e executa o merge (ou fallback se timeout)
- [ ] Logar cada passo com timestamp

**Teste:** nos logs do `docker compose up` ver a sequência completa de mensagens com timings corretos.

---

## Etapa 10 — Estado do protocolo visível no dashboard

- [ ] Bridge WebSocket passa a incluir o estado do protocolo de cada veículo (normal / em conflito / a abrandar / merge executado)
- [ ] Canvas mostra cores diferentes por estado em cada veículo
- [ ] Painel lateral com log de MCMs em tempo real

**Teste:** `docker compose up`, abrir o browser, ver o merge acontecer visualmente sobre os segmentos desenhados.
