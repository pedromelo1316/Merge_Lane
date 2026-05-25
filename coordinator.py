#!/usr/bin/env python3
import argparse
import glob
import json
import os
import sys
import threading
import time

COORDINATOR_ZENOH_URL = os.environ.get("COORDINATOR_ZENOH_URL", "tcp/127.0.0.1:7446")
SCENARIOS_DIR         = os.path.join(os.path.dirname(__file__), "scenarios")
POOL                  = [10, 11, 12, 13]
DEFAULT_TIMEOUT_S     = 60


def open_zenoh_session(url):
    import zenoh
    cfg = '{"mode":"client","connect":{"endpoints":["' + url + '"]}}'
    return zenoh.open(zenoh.Config.from_json5(cfg))


def wait_for_pool_ready(session, pool, timeout=DEFAULT_TIMEOUT_S):
    ready   = set()
    lock    = threading.Lock()
    all_rdy = threading.Event()

    def make_handler(sid):
        def on_ready(_sample):
            with lock:
                if sid not in ready:
                    ready.add(sid)
                    print(f"[coordinator] ready {sid} ({len(ready)}/{len(pool)})")
                if ready.issuperset(pool):
                    all_rdy.set()
        return on_ready

    subs = [session.declare_subscriber(f"coordinator/ready/{sid}", make_handler(sid))
            for sid in pool]
    all_rdy.wait(timeout=timeout)
    for sub in subs:
        sub.undeclare()

    if not all_rdy.is_set():
        with lock:
            missing = sorted(set(pool) - ready)
        print(f"[coordinator] Aviso: sem ready de {missing}. A continuar.")


def wait_for_done(session, active, timeout, after_subscribe):
    # subscrever antes de publicar — Zenoh não tem persistência, done podia perder-se
    done     = set()
    lock     = threading.Lock()
    all_done = threading.Event()

    def make_handler(sid):
        def on_done(_sample):
            with lock:
                done.add(sid)
                print(f"[coordinator] done {sid} ({len(done)}/{len(active)})")
                if done.issuperset(active):
                    all_done.set()
        return on_done

    subs = [session.declare_subscriber(f"coordinator/done/{sid}", make_handler(sid))
            for sid in active]
    after_subscribe()
    all_done.wait(timeout=timeout)
    for sub in subs:
        sub.undeclare()

    if not all_done.is_set():
        with lock:
            missing = sorted(set(active) - done)
        print(f"[coordinator] Aviso: timeout — done em falta: {missing}. A avançar.")


def load_scenarios(name=None):
    if name:
        fname = name if name.endswith(".json") else name + ".json"
        path  = fname if os.path.isabs(fname) else os.path.join(SCENARIOS_DIR, fname)
        if not os.path.isfile(path):
            print(f"[coordinator] Cenário não encontrado: {path}")
            sys.exit(1)
        with open(path) as fh:
            return [(os.path.basename(path), json.load(fh))]

    files = sorted(glob.glob(os.path.join(SCENARIOS_DIR, "*.json")))
    if not files:
        print(f"[coordinator] Nenhum cenário em {SCENARIOS_DIR}/")
        sys.exit(1)
    result = []
    for f in files:
        with open(f) as fh:
            result.append((os.path.basename(f), json.load(fh)))
    return result


def run_scenario(session, name, scenario):
    active    = scenario.get("active_vehicles", [])
    timeout_s = float(scenario.get("timeout_s", DEFAULT_TIMEOUT_S))

    print(f"\n[coordinator] === {name} | active={active} | timeout={timeout_s}s ===")

    if not active:
        print("[coordinator] Sem active_vehicles — a saltar.")
        return

    print("[coordinator] À espera que todos os veículos estejam prontos...")
    wait_for_pool_ready(session, POOL)

    payload = json.dumps(scenario).encode()

    def publish():
        print("[coordinator] A publicar cenário...")
        session.put("coordinator/scenario", payload)
        print(f"[coordinator] À espera de done de {active}...")

    wait_for_done(session, active, timeout_s, after_subscribe=publish)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", help="Cenário a correr (nome, com ou sem .json)")
    args = parser.parse_args()

    print(f"[coordinator] A ligar a {COORDINATOR_ZENOH_URL}...")
    session = open_zenoh_session(COORDINATOR_ZENOH_URL)
    print("[coordinator] Ligado.")

    scenarios = load_scenarios(args.scenario)
    print(f"[coordinator] {len(scenarios)} cenário(s) encontrado(s).")

    for name, scenario in scenarios:
        run_scenario(session, name, scenario)
        time.sleep(1.0)

    print("\n[coordinator] Todos os cenários concluídos.")
    session.close()


if __name__ == "__main__":
    main()
