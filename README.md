# Fusao cooperativa de via via V2V

## Visao geral

Este projeto simula uma fusao cooperativa de vias usando comunicacao V2X. Um veiculo na rampa (Merge Car) pede autorizacao para entrar na via principal, e os veiculos na via principal coordenam um abrandamento controlado. A troca de mensagens usa CAM e MCM (ETSI C-ITS), com transporte via Vanetza-NAP e Zenoh, e uma dashboard HTML para visualizar a simulacao.

## Cenario e protocolo (resumo)

- Todos os veiculos emitem CAMs periodicamente com posicao e velocidade.
- O veiculo da rampa calcula o instante estimado de chegada ao ponto de conflito e envia um MERGE_REQUEST.
- Veiculos em conflito propagam um SLOWDOWN_REQUEST para tras; o ultimo confirma com ACK, que regressa para a frente.
- O primeiro veiculo em conflito envia MERGE_GRANT ao veiculo da rampa, que valida e executa o merge.

## Tecnologias

- V2X: Vanetza-NAP (ETSI C-ITS)
- Mensagens: CAM e MCM
- Middleware: Zenoh (pub/sub)
- Simulacao: Python 3
- Visualizacao: HTML canvas
- Containerizacao: Docker Compose

## Estrutura do repositorio (resumo)

- bridge.py: ponte WebSocket que recebe CAM/MCM via Zenoh e envia estado para o browser.
- dashboard.html: UI com canvas para desenhar estradas e veiculos em tempo real.
- docker-compose.yml: arranque dos containers Vanetza-NAP (um por veiculo).
- roads.json: definicao das estradas (segmentos GPS) e limites de velocidade.
- vehicles.json: configuracao dos veiculos (IDs, estrada inicial, velocidade).
- run_vehicles.py: lancador local dos 4 veiculos Python, cada um ligado ao seu broker Zenoh.
- simulate.py: simulacao local simples (sem containers), usada nas primeiras etapas.
- TODO.md: roteiro de desenvolvimento com etapas e testes associados.
- CLAUDE.md: descricao detalhada do objetivo, protocolo e decisoes de arquitetura.
- ASN1/: descricoes ASN.1 dos PDU CAM e MCM.
  - CAM-PDU-Descriptions.asn
  - MCM-PDU-Descriptions.asn
- examples/: exemplos de mensagens CAM/MCM e um script para gerar/enviar.
  - *.json, *.txt: exemplos de payloads
  - generate.py: exemplo de como enviar mensagens via Vanetza-NAP e Zenoh
- vehicle/: logica do veiculo e builder de CAM.
  - cam_builder.py: funcao pura para construir CAM.
  - vehicle.py: logica principal do veiculo (movimento, CAMs, deteccao de conflito).

## Nota sobre o estado do projeto

O ficheiro TODO.md indica o que ja foi implementado e o que falta concluir, incluindo a construcao dos builders MCM e o protocolo completo de merge.
