# Roadmap de Mini-Steps — Cooperative Lane Merge

## Step 1 — Posições iniciais reais nos CAMs

**O quê:** Definir um `VEHICLE_STATE` em `generate.py` com lat/lon/speed/heading inicial por veículo. Atualizar `load_cam_payload()` para usar esses valores em vez de zeros.

**Ficheiro:** `examples/generate.py`

**Resultado verificável:** correr `python3 examples/generate.py a` publica CAMs com a posição real do Vehicle A; correr `python3 examples/generate.py mc` publica com a posição do MC. As posições são distintas e visíveis no subscriber.

---

## Step 2 — Definir os subtypes de MCM

**O quê:** Criar constantes (`MERGE_REQUEST`, `SLOWDOWN_REQUEST`, `ACK`, `MERGE_GRANT`) mapeadas ao campo `mcmType` do payload. Criar funções builder: `build_merge_request(mc_position, eta)`, `build_slowdown_request(sender_id, target_id)`, `build_ack(sender_id)`, `build_merge_grant(sender_id)`.

**Ficheiro:** `examples/generate.py` ou novo `examples/mcm_protocol.py`

**Resultado verificável:** chamar cada builder devolve um payload JSON válido que o Vanetza aceita sem erro.

---

## Step 3 — MC envia MERGE_REQUEST real

**O quê:** Substituir o `mcm_publisher_loop` genérico por um que envia um `MERGE_REQUEST` com a posição do MC e o ETA calculado até ao conflict zone (posição hardcoded). Só envia quando a distância ao conflict zone for menor que um threshold.

**Resultado verificável:** subscriber nas outras 3 janelas recebe MCMs com `mcmType=MERGE_REQUEST` e stationID=10.

---

## Step 4 — Vehicle A reage ao MERGE_REQUEST

**O quê:** No `on_sample`, se a mensagem recebida for um MCM do tipo `MERGE_REQUEST` e o veículo for A, este envia um `SLOWDOWN_REQUEST` para B (publicar no próprio broker em `vanetza/in/mcm`). Se A não estiver em conflito, envia `MERGE_GRANT` diretamente.

**Resultado verificável:** janela do Vehicle B recebe `SLOWDOWN_REQUEST`.

---

## Step 5 — Propagação da chain (B→C slowdown, C→B ACK, B→A ACK)

**O quê:** B recebe `SLOWDOWN_REQUEST`, propaga para C. C (sem veículo atrás) envia `ACK` para B. B repassa `ACK` para A.

**Resultado verificável:** A recebe ACK depois de toda a cadeia.

---

## Step 6 — A envia MERGE_GRANT ao MC

**O quê:** Depois de receber o ACK de B, A envia `MERGE_GRANT` para o MC.

**Resultado verificável:** MC recebe `MERGE_GRANT`, imprime "merge granted".

---

## Step 7 — MC valida e conclui o merge

**O quê:** MC acumula os `MERGE_GRANT` esperados (de todos os veículos em conflito) e confirma com os últimos CAMs recebidos. Se tudo ok, imprime "MERGE EXECUTED". Se timeout, reenviar `MERGE_REQUEST`.

**Resultado verificável:** protocolo completo end-to-end visível nos logs das 4 janelas.

---

## Verificação final

Abrir 4 terminais, cada um a correr `generate.py` com um veículo diferente (`mc`, `a`, `b`, `c`). Observar nos logs:

1. CAMs com posições distintas por veículo
2. `mc` envia `MERGE_REQUEST`
3. `a` → `b` → `c` propagam `SLOWDOWN_REQUEST`
4. `c` → `b` → `a` propagam `ACK`
5. `a` envia `MERGE_GRANT` ao `mc`
6. `mc` imprime `MERGE EXECUTED`
