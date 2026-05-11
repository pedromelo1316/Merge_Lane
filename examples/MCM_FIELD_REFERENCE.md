# MCM Field Reference — Vanetza-NAP JSON Codec

Referência dos valores válidos para cada campo das mensagens MCM publicadas em `vanetza/in/mcm`.
Baseado na análise do codec `tools/socktap/asn1json.cpp` e do schema `asn1/MCM-PDU-Descriptions.asn`.

> **Nota sobre escalas**: muitos campos são escalados pelo codec antes de serem codificados em ASN.1/UPER.
> O valor no JSON **não é** o valor ASN.1 — lê a coluna "Escala" antes de usar.

---

## basicContainer

### `generationDeltaTime`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` (float) |
| Unidade JSON | segundos Unix (timestamp) |
| Escala codec | × 1000 → ms |
| Codec | `generationDeltaTime * 1000` → `asn_long2INTEGER` |
| Exemplo | `1741192835.4648783` |

### `stationID`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Range | 0..4294967295 |
| Exemplo | `10` (MC), `11` (veículo A) |

### `stationType`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Valores válidos | `1` = vehicle |
| Exemplo | `1` |

### `itssRole`
| Código | Significado |
|---|---|
| `0` | notAvailable |
| `1` | coordinatingItss (MC — pede merge) |
| `2` | notCoordinatingSubjectVehicle |
| `3` | targetVehicle (veículo que pede ao outro para abrandar) |

### `position`

#### `latitude` / `longitude`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` (float) |
| Unidade JSON | graus decimais |
| Escala codec | × 10^7 → ASN.1 units (10^-7 graus) |
| Range JSON | latitude: -90.0..90.0 / longitude: -180.0..180.0 |
| Sentinel | `900000001` (latitude unavailable) |
| Exemplo | `40.638572`, `-8.649928` |

#### `positionConfidenceEllipse.semiMajorAxisLength` / `semiMinorAxisLength`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Unidade ASN.1 | 0.01 m |
| Range | 0..4095 (4094=outOfRange, 4095=unavailable) |
| Exemplo | `0` (desconhecido) |

#### `positionConfidenceEllipse.semiMajorAxisOrientation`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Range | 0..3601 (3601=unavailable) |
| Unidade | 0.1 graus, a partir do Norte |

#### `altitude.altitudeValue`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Unidade ASN.1 | 0.01 m |
| Range | -100000..800001 (-100000=unavailable, 800001=unavailable) |
| Exemplo | `2` (= 0.02 m, aproximadamente ao nível do mar) |

#### `altitude.altitudeConfidence`
| Código | Significado |
|---|---|
| `0` | alt-000-01 (±0.01 m) |
| `1` | alt-000-02 |
| ... | ... |
| `14` | alt-200-00 |
| `15` | outOfRange |

### `mcmType`
| Código | Significado |
|---|---|
| `0` | intent |
| `1` | **request** (usado em MERGE_REQUEST e SLOWDOWN_REQUEST) |
| `2` | response |
| `3` | reservation |
| `4` | termination |
| `5` | cancellationRequest |
| `6` | emergencyManoeuvreReservation |
| `7` | executionStatus |
| `8` | offer |
| `9` | acknowledgment |

### `manoeuvreId`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Tipo ASN.1 | `Identifier1B` |
| Range | 0..255 |
| Exemplo | `42` |

### `concept`
| Código | Significado |
|---|---|
| `0` | **agreementSeeking** (usar com `manoeuvreCooperationCost`) |
| `1` | prescriptive (usar com `manoeuvreCooperationGoal`) |

### `rational` (OPTIONAL)
CHOICE — apenas uma das chaves deve estar presente:

#### `rational.manoeuvreCooperationCost`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Range | 0..100 |
| Usar quando | `concept: 0` (agreementSeeking) |
| Exemplo | `0` |

#### `rational.manoeuvreCooperationGoal`
| Código | Significado |
|---|---|
| `0` | vehicleInterception |
| `1` | roadSafety |
| `2` | humanHelth |
| `3` | emergencyIntervention |
| `4` | roadOperatorIntervention |
| `5` | localTrafficManagement |
| `6` | globalTrafficManagement |
| Usar quando | `concept: 1` (prescriptive) |

---

## mcmContainer.vehicleManoeuvreContainer

### vehicleCurrentStateContainer

#### `vehicleSpeed.speedValue`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` (float) |
| **Unidade JSON** | **m/s** |
| Escala codec | × 100 → ASN.1 (0.01 m/s) |
| Range JSON | 0.00..163.83 m/s |
| Sentinel JSON | `16383` (passed as-is = unavailable) |
| Exemplos | `11.11` ≈ 40 km/h · `22.22` ≈ 80 km/h · `16.67` ≈ 60 km/h |
| **ATENÇÃO** | Valores > 163.83 causam falha UPER silenciosa |

#### `vehicleSpeed.speedConfidence`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` |
| Escala codec | × 100 (excepto sentinels 126 e 127) |
| Sentinels JSON | `126` = outOfRange, `127` = unavailable (passed as-is) |
| Range JSON | 0.01..1.25 (precisão em m/s) ou 126 / 127 |
| Exemplo | `127` (unavailable) |

#### `vehicleHeading.value`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Escala codec | **nenhuma** (valor directo) |
| Unidade | 0.1 graus, sentido horário a partir do Norte |
| Range | 0..3601 |
| Valores nomeados | `0`=Norte · `900`=Este · `1800`=Sul · `2700`=Oeste · `3600`=doNotUse · `3601`=unavailable |
| Exemplos | `900` (90° Este), `0` (Norte), `3601` (desconhecido) |

#### `vehicleHeading.confidence`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Escala codec | nenhuma |
| Range | 1..127 (126=outOfRange, 127=unavailable) |
| Exemplo | `127` (unavailable) |

#### `vehicleSize.vehicleType`
Tipo `Iso3833VehicleType` — ver norma ISO 3833. Exemplos comuns:
| Código | Significado |
|---|---|
| `0` | unknown |
| `1` | moped |
| `2` | motorcycle |
| `3` | passengerCar |
| `4` | bus |
| `5` | lightTruck |
| `6` | heavyTruck |
| `7` | trailer |
| `8` | specialVehicle |
| `9` | tram |

#### `vehicleSize.vehicleLenth.vehicleLengthValue`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` |
| **Unidade JSON** | **metros** |
| Escala codec | × 10 → ASN.1 (0.1 m) |
| Range JSON | 0.1..102.1 m |
| Sentinel JSON | `1023` (passed as-is = unavailable) |
| Exemplos | `4` = 4.0 m · `40` = 40.0 m (camião) |

#### `vehicleSize.vehicleLenth.vehicleLengthConfidenceIndication`
| Código | Significado |
|---|---|
| `0` | noTrailerPresent |
| `1` | trailerPresentWithKnownLength |
| `2` | trailerPresentWithUnknownLength |
| `3` | trailerPresenceIsUnknown |
| `4` | **unavailable** |

#### `vehicleSize.vehicleWidth`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` |
| **Unidade JSON** | **metros** |
| Escala codec | × 10 → ASN.1 (0.1 m) |
| Range JSON | 0.1..6.0 m |
| Sentinels JSON | `61` = outOfRange, `62` = unavailable (passed as-is) |
| **ATENÇÃO** | Valores > 6.0 causam constraint violation (61×10=610 > 62 ASN.1 max) |
| Exemplos | `2` = 2.0 m (ligeiro) · `2.5` = 2.5 m · `6` = 6.0 m (máximo normal) |

#### `vehicleSize.vehicleHeight`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Escala codec | **nenhuma** (valor directo) |
| Unidade ASN.1 | 0.1 m |
| Range | 1..128 (127=unavailable, 128=?) |
| Exemplos | `1` = 0.1 m (placeholder) · `15` = 1.5 m · `20` = 2.0 m |

---

### submaneuvres

#### `submanoeuvreID`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Range | 0..255 |
| Exemplo | `0` |

#### `referenceTrajectory.wayPointType`
| Código | Significado |
|---|---|
| `1` | valor usado nos exemplos funcionais |

#### `referenceTrajectory.wayPoints[].pathPosition.deltaLatitude` / `deltaLongitude`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` |
| **Unidade JSON** | **graus** (offset relativo à posição de referência) |
| Escala codec | × 10^7 → ASN.1 |
| Range JSON | -0.0131071..0.0131071 graus (~1.46 km máx.) |
| Sentinel JSON | `131072` = unavailable |
| **ATENÇÃO** | Valores > 0.013 graus causam constraint violation após scaling |
| Valor seguro | `0` (sem offset) |

#### `referenceTrajectory.wayPoints[].pathPosition.deltaAltitude`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `number` |
| Unidade JSON | metros (offset de altitude) |
| Escala codec | × 100 → ASN.1 |
| Range JSON | -127.0..127.98 m |
| Sentinels | `-12700`, `12799`, `12800` (passed as-is) |
| Valor seguro | `0` |

#### `referenceTrajectory.speed[].speedValue` / `speedConfidence`
Mesmas regras de `vehicleSpeed.speedValue` e `vehicleSpeed.speedConfidence` — ver acima.

#### `temporalCharateristics.tRROccupancyStartTime` / `tRROccupancyEndTime`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Unidade | ms |
| Range | 0..65535 |
| Exemplos | `1000` = 1 s · `5000` = 5 s |
| **Nota** | Typo intencional: `"temporalCharateristics"` (com um 'a' a menos) — corresponde ao nome no schema ASN.1 |

#### `targetRoadResourceIContainer` (OPTIONAL)
Pode ser omitido. Quando presente:

| Campo | Tipo | Range / Valores |
|---|---|---|
| `trrType` | `integer` | tipo de recurso rodoviário (ex: `2`) |
| `laneCount` | `integer` | número de faixas (ex: `2`) |
| `waypoints[].pathPosition` | object | mesmas regras de `deltaLatitude/Longitude/Altitude` |
| `heading[].value` | `integer` | 0..3601 (0.1°) |
| `heading[].confidence` | `integer` | 1..127 |
| `trrWidth` | `integer` | largura do recurso |
| `trrLength` | `integer` | comprimento do recurso |

---

### manoeuvreAdvice (OPTIONAL)

#### `executantID`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Tipo ASN.1 | `StationId` |
| Range | 0..4294967295 |
| Exemplos | `11`, `12` (stationIDs dos veículos alvo) |

#### `submaneuvres[].submanoeuvreId`
| Propriedade | Valor |
|---|---|
| Tipo JSON | `integer` |
| Range | 0..255 |

#### `submaneuvres[].advisedTrajectory`
Mesma estrutura de `referenceTrajectory` — incluir para dar conselhos de velocidade.
`advisedTrajectory` é **OPTIONAL** (omitir se não houver conselho de trajectória).

#### `submaneuvres[].advisedTargetRoadResource` (OPTIONAL)
Quando presente, requer dois campos obrigatórios:
- `trrDescription` — mesma estrutura de `targetRoadResourceIContainer`
- `temporalCharacteristics` (nota: esta versão tem ortografia **correcta**) com `tRROccupancyStartTime` e `tRROccupancyEndTime`

---

## Resumo de escalas do codec

| Campo JSON | Unidade JSON | Factor | Unidade ASN.1 | Máximo JSON | Sentinel(s) |
|---|---|---|---|---|---|
| `speedValue` | m/s | ×100 | 0.01 m/s | 163.83 | `16383` |
| `speedConfidence` | m/s (precisão) | ×100 | 0.01 m/s | 1.25 | `126`, `127` |
| `vehicleWidth` | m | ×10 | 0.1 m | 6.0 | `61`, `62` |
| `vehicleLengthValue` | m | ×10 | 0.1 m | 102.1 | `1023` |
| `vehicleHeight` | 0.1 m | ×1 | 0.1 m | 12.8 | — |
| `vehicleHeading.value` | 0.1° | ×1 | 0.1° | 3601 | `3601` |
| `deltaLatitude/Longitude` | graus | ×10^7 | 10^-7 ° | 0.0131071° | `131072` |
| `deltaAltitude` | m | ×100 | 0.01 m | 127.98 | `-12700`, `12799`, `12800` |
| `generationDeltaTime` | s (Unix) | ×1000 | ms | — | — |
| `latitude/longitude` (posição) | graus | ×10^7 | 10^-7 ° | ±90° / ±180° | `900000001` |

---

## Falhas silenciosas (sem mensagem de erro nos logs)

Quando `speedValue * 100 > 16383` ou `vehicleWidth * 10 > 62`, o `Application::request()` em
`mcm_application.cpp` retorna `false` sem lançar excepção. Neste caso:
- **Nenhuma mensagem de erro** é impressa nos logs do container
- **Nada aparece** em `vanetza/time/mcm`

Para diagnosticar, activar `debug_enabled = true` em `config.ini` — o codec chamará `message.validate()`
antes de tentar codificar, e imprimirá o erro de constraint violation.
