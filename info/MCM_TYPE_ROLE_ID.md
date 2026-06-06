# MCM — Valores de `mcmType`, `itssRole` e `manoeuvreId`

Valores oficiais retirados do schema `MCM-PDU-Descriptions.asn` (enumerações ASN.1) e do
`MCM_FIELD_REFERENCE.md` (codec Vanetza-NAP). A coluna "Uso no projeto" mapeia cada valor
para os subtipos do protocolo de merge cooperativo.

---

## `mcmType` — `McmType` (ENUMERATED)

Identifica o tipo de mensagem de coordenação de manobra.

| Código | Nome ASN.1 | Significado | Uso no projeto |
|---|---|---|---|
| `0` | `intent` | Anúncio de intenção de manobra | — |
| `1` | `request` | Pedido de manobra | **MERGE_REQUEST** e **SLOWDOWN_REQUEST** |
| `2` | `response` | Resposta a um pedido | **SLOWDOWN_GRANT**, **MERGE_GRANT**, **MERGE_CONFIRMED** |
| `3` | `reservation` | Reserva de recurso rodoviário | — |
| `4` | `termination` | Terminação da manobra | — |
| `5` | `cancellationRequest` | Pedido de cancelamento | — |
| `6` | `emergencyManoeuvreReservation` | Reserva para manobra de emergência | — |
| `7` | `executionStatus` | Estado de execução da manobra | **EXECUTION_STATUS** |
| `8` | `offer` | Oferta de cooperação | — |
| `9` | `acknowledgment` | Confirmação de receção | builder `ACK` existe em `mcm_builder.py`, mas **não usado** (substituído por SLOWDOWN_GRANT) |

---

## `itssRole` — `McmItssRole` (ENUMERATED)

Papel da ITS-S originadora da mensagem.

| Código | Nome ASN.1 | Significado | Uso no projeto |
|---|---|---|---|
| `0` | `notAvailable` | Indisponível | — |
| `1` | `coordinatingItss` | ITS-S coordenadora (quem inicia/coordena o merge) | **MC** — em MERGE_REQUEST, MERGE_CONFIRMED, EXECUTION_STATUS |
| `2` | `notCoordinatingSubjectVehicle` | Veículo participante mas não coordenador | — |
| `3` | `targetVehicle` | Veículo-alvo (a quem se pede para abrandar) | **veículos da estrada** — em SLOWDOWN_REQUEST, SLOWDOWN_GRANT, MERGE_GRANT |

---

## `manoeuvreId` — `ManoeuvreId` = `Identifier1B`

Identificador da sessão de coordenação (ID da ITS-S no header, módulo 256).

| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Tipo ASN.1 | `Identifier1B` |
| Range global | **0..255** |

No standard, `manoeuvreId` não tem subdivisões — é apenas um identificador de 1 byte.
**A divisão em gamas é uma convenção do projeto**, usada para distinguir subtipos que
partilham o mesmo `mcmType`+`itssRole`:

| Gama | Subtipo no projeto | Porquê |
|---|---|---|
| `0–127` | MERGE_REQUEST, SLOWDOWN_REQUEST, MERGE_GRANT, MERGE_CONFIRMED, EXECUTION_STATUS | gama "normal" |
| `128–255` | **SLOWDOWN_GRANT** | distingue-o de MERGE_GRANT, que tem o mesmo `mcmType=2`/`itssRole=3` |

---

> **Nota de fidelidade:** as enumerações (códigos + nomes) são as do standard ETSI e são
> fiáveis. O mapeamento "Uso no projeto" e a convenção das gamas de `manoeuvreId` vêm do
> `CLAUDE.md`/`README.md` — confirmar em `vehicle/mcm_builder.py` e `vehicle/protocol.py`
> antes de citar no relatório.
