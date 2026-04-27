"""
Publisher de teste — publica 5 mensagens em v2x/test/hello com intervalo de 1s.
Correr depois de o subscriber estar ativo.

Uso:
    python tests/zenoh_pub.py
"""

import json
import time
import zenoh

ROUTER = "tcp/192.168.98.5:7447"
TOPIC  = "v2x/test/hello"
N_MSGS = 5


def main():
    conf = zenoh.Config()
    conf.insert_json5("connect/endpoints", json.dumps([ROUTER]))
    conf.insert_json5("scouting/multicast/enabled", "false")

    print(f"A ligar ao router: {ROUTER}")
    with zenoh.open(conf) as session:
        print(f"Ligado. A publicar {N_MSGS} mensagens em '{TOPIC}'...\n")
        for i in range(1, N_MSGS + 1):
            msg = {
                "type": "test",
                "seq": i,
                "from": "zenoh_pub_test",
                "ts": time.time(),
            }
            payload = json.dumps(msg)
            session.put(TOPIC, payload)
            print(f"  [{i}/{N_MSGS}] Publicado: {payload}")
            time.sleep(1)
        print("\nPublisher concluído.")


if __name__ == "__main__":
    main()
