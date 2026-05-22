#!/usr/bin/env python3
"""
coordinator.py — corre localmente, orquestra a execução de cenários.
Publica cenários via Zenoh e aguarda que todos os veículos activos reportem done.
"""

import argparse
import glob
import json
import os
import sys
import threading
import time

COORDINATOR_ZENOH_URL = os.environ.get("COORDINATOR_ZENOH_URL", "tcp/127.0.0.1:7446")
SCENARIOS_DIR         = os.path.join(os.path.dirname(__file__), "scenarios")
POOL                  = [10, 11, 12, 13]   # station IDs do pool fixo
DEFAULT_TIMEOUT_S     = 120
READY_TIMEOUT_S       = 60


def open_zenoh_session(url):
    import zenoh
    config_json = '{"mode":"client","connect":{"endpoints":["' + url + '"]}}'
    return zenoh.open(zenoh.Config.from_json5(config_json))


def wait_for_pool_ready(session, pool, timeout=READY_TIMEOUT_S):
    ready    = set()
    lock     = threading.Lock()
    all_rdy  = threading.Event()

    def make_handler(sid):
        def on_ready(_sample):
            with lock:
                if sid not in ready:
                    ready.add(sid)
                    print(f"[coordinator] ready de station_id={sid} "
                          f"({len(ready)}/{len(pool)})")
                if ready.issuperset(pool):
                    all_rdy.set()
        return on_ready

    subs = [session.declare_subscriber(f"coordinator/ready/{sid}", make_handler(sid))
            for sid in pool]

    ok = all_rdy.wait(timeout=timeout)
    for sub in subs:
        sub.undeclare()

    if not ok:
        with lock:
            missing = sorted(set(pool) - ready)
        print(f"[coordinator] Aviso: ready timeout — veículos em falta: {missing}. A continuar.")
    return ok


def wait_for_done(session, active, timeout=DEFAULT_TIMEOUT_S):
    done     = set()
    lock     = threading.Lock()
    all_done = threading.Event()

    def make_handler(sid):
        def on_done(_sample):
            with lock:
                done.add(sid)
                print(f"[coordinator] done de station_id={sid} "
                      f"({len(done)}/{len(active)})")
                if done.issuperset(active):
                    all_done.set()
        return on_done

    subs = [session.declare_subscriber(f"coordinator/done/{sid}", make_handler(sid))
            for sid in active]

    ok = all_done.wait(timeout=timeout)
    for sub in subs:
        sub.undeclare()

    if not ok:
        with lock:
            missing = sorted(set(active) - done)
        print(f"[coordinator] Aviso: timeout no cenário — veículos em falta: {missing}. A avançar.")
    return ok


def load_scenarios(scenario_filter=None):
    if scenario_filter:
        # accept bare name (e.g. "02_colision") or full filename with/without path
        candidate = scenario_filter
        if not candidate.endswith(".json"):
            candidate += ".json"
        path = candidate if os.path.isabs(candidate) else os.path.join(SCENARIOS_DIR, candidate)
        if not os.path.isfile(path):
            print(f"[coordinator] Cenário não encontrado: {path}")
            sys.exit(1)
        with open(path) as fh:
            return [(os.path.basename(path), json.load(fh))]

    pattern = os.path.join(SCENARIOS_DIR, "*.json")
    files   = sorted(glob.glob(pattern))
    if not files:
        print(f"[coordinator] Nenhum cenário encontrado em {SCENARIOS_DIR}/")
        sys.exit(1)
    result = []
    for f in files:
        with open(f) as fh:
            result.append((os.path.basename(f), json.load(fh)))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", nargs="?", default=None,
                        help="Cenário específico a correr (nome do ficheiro, com ou sem .json)")
    parser.add_argument("--demo", action="store_true", default=False,
                        help="Demo mode: insert a step delay between protocol messages")
    parser.add_argument("--demo-delay", type=float, default=5.0,
                        help="Seconds to wait before each protocol response (default 5)")
    args = parser.parse_args()

    if args.demo:
        print(f"[coordinator] Demo mode activo (demo_step_delay={args.demo_delay}s).")

    print(f"[coordinator] A ligar ao Zenoh router em {COORDINATOR_ZENOH_URL}...")
    session = open_zenoh_session(COORDINATOR_ZENOH_URL)
    print("[coordinator] Ligado.")

    scenarios = load_scenarios(args.scenario)
    print(f"[coordinator] {len(scenarios)} cenário(s) encontrado(s).")

    for scenario_name, scenario in scenarios:
        active    = scenario.get("active_vehicles", [])
        timeout_s = float(scenario.get("timeout_s", DEFAULT_TIMEOUT_S))

        print(f"\n[coordinator] === Cenário: {scenario_name} | "
              f"active={active} | timeout={timeout_s}s ===")

        if not active:
            print("[coordinator] Sem active_vehicles — a saltar.")
            continue

        if args.demo:
            scenario["demo_mode"] = True
            scenario["demo_step_delay_s"] = args.demo_delay

        print(f"[coordinator] À espera que todos os veículos do pool estejam prontos...")
        wait_for_pool_ready(session, POOL)

        print(f"[coordinator] A publicar cenário...")
        session.put("coordinator/scenario", json.dumps(scenario).encode())

        print(f"[coordinator] Cenário publicado. À espera de done de {active}...")
        wait_for_done(session, active, timeout=timeout_s)

        time.sleep(1.0)

    print("\n[coordinator] Todos os cenários concluídos.")
    session.close()


if __name__ == "__main__":
    main()
