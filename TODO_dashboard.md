# TODO — Dashboard Improvements

Melhorias incrementais ao dashboard. Implementar uma de cada vez.

---

## 1 — Etapa 17: badges de estado do protocolo por veículo `[requer Docker]`

**Contexto:** TODO.md Etapa 17. O dashboard não tem feedback visual do estado do protocolo por veículo.

- [x] `bridge.py`: inferir SLOWING/SPEEDING a partir de CAMs em `on_cam()`, não MCMs
  - `on_cam()`: extrair `longitudinalAcceleration.value` e usar como fonte de verdade
  - Aceleração < 0 → `SLOWING`; > 0 → `SPEEDING`; == 0 → `NORMAL`
  - Manter `MERGING` como protocolo override (não muda por CAMs)
- [x] `bridge.py`: simplificar `on_mcm()` — remover toda lógica de SLOWING/SPEEDING, manter apenas MERGING
  - `MERGE_CONFIRMED` → MC: `MERGING`
  - `EXECUTION_STATUS` → MC: `NORMAL`
- [x] `cam_builder.py` e `vehicle.py`: passar aceleração real nas CAMs
  - `vehicle.py`: calcular `accel_ms2` (negativa = travagem, positiva = aceleração, 0 = constante)
  - `cam_builder.py`: preencher `longitudinalAcceleration.value = accel_ms2`
- [x] `bridge.py`: incluir `"state"` em cada entrada de `vehicle_states` no broadcast
- [x] `dashboard.html`: em `drawVehicles()`, colorir marcador e glow conforme estado
  - `NORMAL` → branco (MC: ciano, como está)
  - `SLOWING` → laranja `#ff9800`
  - `MERGING` → verde `#4caf50`
  - `SPEEDING` → azul `#2196f3`
- [x] `dashboard.html`: adicionar legenda de cores no canto superior direito
- [x] `dashboard.html`: desenhar badge de texto (9px) com o estado abaixo de cada marcador

---

## 2 — MCM cards clicáveis com Modal `[sem Docker]`

**Contexto:** os cards do MCM LOG mostram pouca informação. Expandir ao clicar.

- [x] `dashboard.html`: estilo CSS para modal overlay (fundo semitransparente, card centrado, animação de entrada)
- [x] `dashboard.html`: cards ganham `cursor: pointer`; `onclick` abre modal com:
  - Cabeçalho colorido com tipo da mensagem
  - Timestamp, `from → to`, manoeuvre_id, success
  - Descrição contextual do que a mensagem representa no protocolo (lookup table estático)
  - Campos extra se presentes (ex: target_speed, ETA)
- [x] `dashboard.html`: fechar com botão ×, click no backdrop, ou tecla Escape
- [x] `bridge.py` (opcional): enriquecer eventos MCM com campos extra:
  - `MERGE_REQUEST`: `eta_ms`, `entry_lat/lon`
  - `SLOWDOWN_REQUEST`: `target_speed_kmh` de `manoeuvreAdvice[0].targetSpeed`

---

## 3 — Extração de velocidade no bridge + Vehicle Info Panel `[requer Docker]`

**Contexto:** bridge só envia posição. Dashboard não tem painel de estado dos veículos.

- [x] `bridge.py`: em `on_cam()`, extrair velocidade do `highFrequencyContainer`:
  ```python
  speed_ms = hf.get("speed", {}).get("speedValue", 0) / 100.0  # ASN.1: cm/s → m/s
  speed_kmh = round(speed_ms * 3.6, 1)
  ```
- [x] `bridge.py`: extrair aceleração e incluir `"speed_kmh"`, `"accel_ms2"` e `"road_id"` (estrada mais próxima) em `vehicle_states`
- [x] `dashboard.html`: adicionar `#vehicle-panel` abaixo do `.main-row` com um card por veículo:
  - ID colorido pelo estado do protocolo
  - Velocidade atual (km/h)
  - Aceleração (m/s²)
  - Posição GPS (lat/lon com 4 casas decimais)
  - Badge de estado (`NORMAL` / `SLOWING` / etc.)
  - Nome da estrada

---

## 4 — Zona de conflito no canvas `[sem Docker]`

**Contexto:** o canvas não identifica visualmente onde ocorre o merge.

- [ ] `dashboard.html`: calcular ponto de merge em JS por interseção geométrica dos dois segmentos de estrada (adaptar lógica de `vehicle.py`)
- [ ] `dashboard.html`: desenhar zona de conflito (círculo semitransparente vermelho, raio proporcional — ~25px fixo ou configurável via broadcast do bridge)

---

## 5 — Timeline do protocolo `[sem Docker]`

**Contexto:** o MCM LOG é uma lista simples; difícil perceber a sequência e timing relativos.

- [ ] `dashboard.html`: adicionar secção colapsável `#protocol-timeline` abaixo do canvas
- [ ] Representação horizontal em escala de tempo: cada MCM como bloco colorido na linha do veículo emissor
- [ ] Hover sobre bloco mostra tooltip com detalhes (tipo, from→to, timestamp)

---

## 6 — Estatísticas do protocolo `[sem Docker]`

**Contexto:** não há visão agregada de quantas negociações ocorreram ou quanto tempo demorou o protocolo.

- [ ] `dashboard.html`: contador de manobras (`manoeuvre_id` únicos), sucesso vs. abort
- [ ] Duração da negociação: `MERGE_REQUEST → EXECUTION_STATUS` (em ms)
- [ ] Exibir como pequeno bloco de stats no canto superior do canvas ou junto ao cabeçalho

---

## Ordem recomendada

| # | Tarefa | Dependências |
|---|--------|-------------|
| 1 | Badges de estado (Etapa 17) | — |
| 2 | MCM cards → Modal | — |
| 3 | Bridge speed + Vehicle Panel | — |
| 4 | Zona de conflito no canvas | — |
| 5 | Timeline do protocolo | — |
| 6 | Estatísticas | — |
