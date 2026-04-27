# Processo de implementação do projeto

## 1. Infraestrutura Base

### Rede docker

Foi criada a rede Docker `vanetzalan0` para que os containers Vanetza possam trocar mensagens ETSI C-ITS.

```bash
docker network create vanetzalan0 --subnet 192.168.98.0/24
```

### Docker-compose.yml só com zenoh

Foi criado um `docker-compose.yml` para o container do Zenoh, que é a base de comunicação entre os veículos.

```yaml
  zenoh-router:
    image: eclipse/zenoh:latest
    container_name: zenoh-router
    networks:
      vanetzalan0:
        ipv4_address: 192.168.98.5
    ports:
      - "7447:7447"      # TCP (ligações dos clientes)
      - "7447:7447/udp"  # UDP (scouting multicast)
    restart: unless-stopped
```

### Teste Zenoh pub/sub

Foi criado um scirpt pub.py que envia 5 mensagens para o tópico `v2x/test/hello` e um script sub.py que subscreve a esse tópico e imprime as mensagens recebidas.

## 2. Adicionar um unico veículo

### Docker-compose.yml com um veículo

Foi adicionado o container `mc` (Merge Car) ao `docker-compose.yml`, com a imagem `ghcr.io/nap-it/vanetza-nap`, estendida por um `Dockerfile` local que instala Python 3 e o pacote `eclipse-zenoh`.

O container tem IP fixo `192.168.98.10`, Station ID 10, e o `vehicle_sim.py` é montado como volume e arranca após o Vanetza:

```yaml
command: >
  sh -c "/entrypoint.sh &
         sleep 3 &&
         python3 /vehicle_sim.py"
```

Neste ponto já era possível ver os CAMs periódicos do Vanetza a aparecer no tópico `vanetza/in/cam` do router Zenoh.

### Simulador do veículo

Foi implementado o `vehicle_sim.py` com a lógica de simulação do veiculo. O script lê a configuração via variáveis de ambiente (`VEHICLE_ID`, `VEHICLE_ROLE`, posição inicial, etc.), abre uma sessão Zenoh, e corre três threads em paralelo:

- **`physics_loop`** — actualiza posição e velocidade a 10 Hz
- **`cam_injector`** — publica a posição actualizada em `vanetza/in/cam` a cada 500 ms
- **loop principal** — imprime estado a cada segundo (posição, velocidade, GPS, neighbours)

## 3. Vários veículos

### Docker-compose.yml com vários veículos

Foram adicionados os containers `vehicle-a`, `vehicle-b` e `vehicle-c` ao `docker-compose.yml`, cada um com a imagem Vanetza-NAP e o `vehicle_sim.py` montado como volume.

Configuração única por veículo:

| Container  | IP             | Station ID | MAC                | x inicial |
|------------|----------------|------------|--------------------|-----------|
| vehicle-a  | 192.168.98.11  | 11         | 6e:06:e0:03:00:11  | 130.0 m   |
| vehicle-b  | 192.168.98.12  | 12         | 6e:06:e0:03:00:12  | 160.0 m   |
| vehicle-c  | 192.168.98.13  | 13         | 6e:06:e0:03:00:13  | 190.0 m   |

Todos com `VEHICLE_ROLE=main_road`, `y=0`, `speed=14 m/s`, `heading=90°`.

**Nota:** As CAMs publicadas pelo `vehicle_sim.py` em `vanetza/in/cam` não estão a ser reflectidas em `vanetza/out/cam` nos outros containers. O Vanetza gera os seus próprios beacons periódicos com a posição GPS hardcoded, mas não usa as injecções do simulador para actualizar a posição transmitida. A causa exacta (formato de injecção, parsing no `handle_cam_out`, ou limitação do Vanetza-NAP) ainda está por investigar.