import argparse
import subprocess
import sys
import threading

VEHICLES = [
    {"id": "MC", "broker": "tcp/192.168.98.10:7447"},
    {"id": "A",  "broker": "tcp/192.168.98.11:7447"},
    {"id": "B",  "broker": "tcp/192.168.98.12:7447"},
    {"id": "C",  "broker": "tcp/192.168.98.13:7447"},
]


def stream_output(proc, label):
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Simulation speed multiplier (e.g. 2 = 2x faster)")
    args = parser.parse_args()

    procs = []
    threads = []

    for v in VEHICLES:
        proc = subprocess.Popen(
            [sys.executable, "-u", "vehicle/vehicle.py", v["id"],
             "--broker", v["broker"], "--speed", str(args.speed)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(proc)
        t = threading.Thread(target=stream_output, args=(proc, v["id"]), daemon=True)
        t.start()
        threads.append(t)

    try:
        for proc in procs:
            proc.wait()
    except KeyboardInterrupt:
        print("\n[run_vehicles] a parar...")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait()


if __name__ == "__main__":
    main()
