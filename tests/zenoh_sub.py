
import json
import time
import zenoh

ROUTER = "tcp/192.168.98.5:7447"
TOPIC  = "vanetza/**"


def on_message(sample):
    ts = time.strftime("%H:%M:%S")
    key = str(sample.key_expr)
    try:
        payload = json.loads(bytes(sample.payload).decode())
        body = json.dumps(payload, indent=2)
    except Exception:
        body = bytes(sample.payload).decode(errors="replace")
    print(f"[{ts}] {key}\n{body}\n")


def main():
    conf = zenoh.Config()
    conf.insert_json5("connect/endpoints", json.dumps([ROUTER]))
    conf.insert_json5("scouting/multicast/enabled", "false")

    print(f"A ligar ao router: {ROUTER}")
    with zenoh.open(conf) as session:
        print(f"Ligado. A subscrever '{TOPIC}' — aguarda mensagens... (Ctrl+C para sair)\n")
        sub = session.declare_subscriber(TOPIC, on_message)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nSubscriber encerrado.")


if __name__ == "__main__":
    main()
