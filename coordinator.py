#!/usr/bin/env python3
import glob
import json
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

COORDINATOR_ZENOH_URL = os.environ.get("COORDINATOR_ZENOH_URL", "tcp/127.0.0.1:7446")
SCENARIOS_DIR         = os.path.join(os.path.dirname(__file__), "scenarios")
POOL                  = [10, 11, 12, 13]
POOL_LABELS           = {10: "MC", 11: "A", 12: "B", 13: "C"}
DEFAULT_TIMEOUT_S     = 60
DEMO_TIMEOUT_EXTRA_S  = 120  # buffer para o modo demo (5s * N mensagens + retry 21s)

_STATUS_COLOR = {
    "idle":    "#888888",
    "waiting": "#f0a500",
    "done":    "#27ae60",
    "timeout": "#e74c3c",
}
_STATUS_SYMBOL = {
    "idle":    "○",
    "waiting": "◑",
    "done":    "●",
    "timeout": "✕",
}


# ─── Core logic (pure, no GUI dependencies) ──────────────────────────────────

def open_zenoh_session(url):
    import zenoh
    cfg = '{"mode":"client","connect":{"endpoints":["' + url + '"]}}'
    return zenoh.open(zenoh.Config.from_json5(cfg))


def load_scenarios(directory):
    files = sorted(glob.glob(os.path.join(directory, "*.json")))
    result = []
    for f in files:
        with open(f) as fh:
            result.append((os.path.basename(f), json.load(fh)))
    return result


def wait_for_pool_ready(session, pool, timeout, log_cb, on_vehicle_cb=None):
    ready   = set()
    lock    = threading.Lock()
    all_rdy = threading.Event()

    def make_handler(sid):
        def on_ready(_sample):
            with lock:
                if sid not in ready:
                    ready.add(sid)
                    log_cb(f"[coordinator] ready {sid} ({len(ready)}/{len(pool)})")
                    if on_vehicle_cb:
                        on_vehicle_cb(sid, "done")  # reuse done colour for "ready"
                if ready.issuperset(pool):
                    all_rdy.set()
        return on_ready

    subs = [session.declare_subscriber(f"coordinator/ready/{sid}", make_handler(sid))
            for sid in pool]
    try:
        all_rdy.wait(timeout=timeout)
    finally:
        for sub in subs:
            sub.undeclare()

    if not all_rdy.is_set():
        with lock:
            missing = sorted(set(pool) - ready)
        log_cb(f"[coordinator] Aviso: sem ready de {missing}. A continuar.")


def wait_for_done(session, active, timeout, after_subscribe, log_cb, on_vehicle_cb=None):
    # Subscribers declared before publishing so no DONE message is missed.
    done     = set()
    lock     = threading.Lock()
    all_done = threading.Event()

    def make_handler(sid):
        def on_done(_sample):
            with lock:
                done.add(sid)
                log_cb(f"[coordinator] done {sid} ({len(done)}/{len(active)})")
                if on_vehicle_cb:
                    on_vehicle_cb(sid, "done")
                if done.issuperset(active):
                    all_done.set()
        return on_done

    subs = [session.declare_subscriber(f"coordinator/done/{sid}", make_handler(sid))
            for sid in active]
    try:
        after_subscribe()
        all_done.wait(timeout=timeout)
    finally:
        for sub in subs:
            sub.undeclare()

    if not all_done.is_set():
        with lock:
            missing = sorted(set(active) - done)
        log_cb(f"[coordinator] Aviso: timeout — done em falta: {missing}. A avançar.")
        if on_vehicle_cb:
            for sid in missing:
                on_vehicle_cb(sid, "timeout")


# ─── GUI ─────────────────────────────────────────────────────────────────────

class CoordinatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Coordinator")
        self.minsize(720, 520)

        self._session      = None
        self._session_lock = threading.Lock()
        self._scenarios    = []
        self._vehicle_dots = {}     # sid -> tk.Label
        self._demo_var     = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_scenarios()
        self._connect_async()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # Status bar
        bar = tk.Frame(self, bd=1, relief=tk.SUNKEN, pady=3, padx=8)
        bar.pack(side=tk.TOP, fill=tk.X)
        self._conn_dot   = tk.Label(bar, text="●", fg=_STATUS_COLOR["idle"],
                                     font=("Helvetica", 13))
        self._conn_dot.pack(side=tk.LEFT)
        self._conn_label = tk.Label(bar, text="Disconnected", anchor=tk.W)
        self._conn_label.pack(side=tk.LEFT, padx=(4, 16))
        tk.Button(bar, text="Reconnect", command=self._reconnect).pack(side=tk.LEFT)

        # Middle pane
        middle = tk.Frame(self)
        middle.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left: scenario list
        left = tk.LabelFrame(middle, text="Scenarios", padx=4, pady=4)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(left, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(left, yscrollcommand=sb.set,
                                    selectmode=tk.SINGLE, activestyle="dotbox",
                                    font=("Courier", 10))
        sb.config(command=self._listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        btns = tk.Frame(left)
        btns.pack(fill=tk.X, pady=(6, 0))
        self._btn_one = tk.Button(btns, text="Run Selected",
                                   command=self._run_selected, state=tk.DISABLED)
        self._btn_one.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self._btn_all = tk.Button(btns, text="Run All",
                                   command=self._run_all, state=tk.DISABLED)
        self._btn_all.pack(side=tk.LEFT, fill=tk.X, expand=True)

        demo_row = tk.Frame(left)
        demo_row.pack(fill=tk.X, pady=(4, 0))
        self._demo_cb = tk.Checkbutton(demo_row, text="Demo Mode",
                                        variable=self._demo_var,
                                        font=("Helvetica", 9))
        self._demo_cb.pack(side=tk.LEFT)

        # Right: vehicle status
        right = tk.LabelFrame(middle, text="Vehicle Status", padx=6, pady=6, width=210)
        right.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)

        hdr = tk.Frame(right)
        hdr.pack(fill=tk.X, pady=(0, 4))
        for txt, w in [("ID", 6), ("Station", 9), ("Status", 7)]:
            tk.Label(hdr, text=txt, font=("Helvetica", 9, "bold"),
                     width=w, anchor=tk.CENTER).pack(side=tk.LEFT)

        for sid in POOL:
            row = tk.Frame(right)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=POOL_LABELS.get(sid, str(sid)),
                     width=6, anchor=tk.CENTER).pack(side=tk.LEFT)
            tk.Label(row, text=str(sid),
                     width=9, anchor=tk.CENTER).pack(side=tk.LEFT)
            dot = tk.Label(row, text=_STATUS_SYMBOL["idle"],
                           fg=_STATUS_COLOR["idle"],
                           font=("Helvetica", 15), width=4, anchor=tk.CENTER)
            dot.pack(side=tk.LEFT)
            self._vehicle_dots[sid] = dot

        legend = tk.LabelFrame(right, text="Legend", padx=4, pady=2)
        legend.pack(fill=tk.X, pady=(12, 0))
        for sym, lbl, col in [
            ("○", "idle",    _STATUS_COLOR["idle"]),
            ("◑", "waiting", _STATUS_COLOR["waiting"]),
            ("●", "done",    _STATUS_COLOR["done"]),
            ("✕", "timeout", _STATUS_COLOR["timeout"]),
        ]:
            row = tk.Frame(legend)
            row.pack(anchor=tk.W)
            tk.Label(row, text=sym, fg=col, width=3, font=("Helvetica", 12)).pack(side=tk.LEFT)
            tk.Label(row, text=lbl, fg="#555555",
                     font=("Helvetica", 8)).pack(side=tk.LEFT)

        # Log panel
        log_frame = tk.LabelFrame(self, text="Log", padx=4, pady=4)
        log_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(0, 8))
        log_toolbar = tk.Frame(log_frame)
        log_toolbar.pack(fill=tk.X, pady=(0, 2))
        tk.Button(log_toolbar, text="Clear", command=self._clear_log,
                  pady=1).pack(side=tk.RIGHT)
        vsb = tk.Scrollbar(log_frame, orient=tk.VERTICAL)
        self._log = tk.Text(log_frame, height=9, state=tk.DISABLED,
                             wrap=tk.WORD, yscrollcommand=vsb.set,
                             font=("Courier", 9))
        vsb.config(command=self._log.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log.pack(fill=tk.X)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clear_log(self):
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _append_log(self, msg):
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _set_vehicle(self, sid, status):
        dot = self._vehicle_dots.get(sid)
        if dot:
            dot.config(text=_STATUS_SYMBOL[status], fg=_STATUS_COLOR[status])

    def _reset_vehicles(self, active):
        for sid in POOL:
            self._set_vehicle(sid, "waiting" if sid in active else "idle")

    def _set_buttons(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self._btn_all.config(state=state)
        self._demo_cb.config(state=state)
        sel = self._scenario_selected()
        self._btn_one.config(state=tk.NORMAL if (enabled and sel is not None) else tk.DISABLED)

    def _set_conn_ui(self, state):
        if state == "connected":
            self._conn_dot.config(fg="#27ae60")
            self._conn_label.config(text=f"Connected  —  {COORDINATOR_ZENOH_URL}")
        elif state == "connecting":
            self._conn_dot.config(fg=_STATUS_COLOR["waiting"])
            self._conn_label.config(text="Connecting…")
        else:
            self._conn_dot.config(fg=_STATUS_COLOR["timeout"])
            self._conn_label.config(text="Disconnected")

    def _scenario_selected(self):
        sel = self._listbox.curselection()
        return sel[0] if sel else None

    def _on_listbox_select(self, _event):
        with self._session_lock:
            has_session = self._session is not None
        if self._scenario_selected() is not None and has_session:
            self._btn_one.config(state=tk.NORMAL)

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect_async(self):
        self._set_conn_ui("connecting")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            session = open_zenoh_session(COORDINATOR_ZENOH_URL)
            with self._session_lock:
                if self._session:
                    try:
                        self._session.close()
                    except Exception:
                        pass
                self._session = session
            self.after(0, self._set_conn_ui, "connected")
            self.after(0, self._append_log,
                       f"[coordinator] Ligado a {COORDINATOR_ZENOH_URL}.")
            self.after(0, self._set_buttons, True)
        except Exception as e:
            self.after(0, self._set_conn_ui, "error")
            self.after(0, self._append_log, f"[coordinator] Falha na ligação: {e}")

    def _reconnect(self):
        self._set_buttons(False)
        self._connect_async()

    # ── Scenarios ─────────────────────────────────────────────────────────────

    def _load_scenarios(self):
        try:
            self._scenarios = load_scenarios(SCENARIOS_DIR)
        except Exception as e:
            self._append_log(f"[coordinator] Erro ao carregar cenários: {e}")
            return
        for name, data in self._scenarios:
            display = data.get("name", "")
            entry   = f"{name}  —  {display}" if display else name
            self._listbox.insert(tk.END, entry)
        self._append_log(
            f"[coordinator] {len(self._scenarios)} cenário(s) carregado(s).")

    def _run_selected(self):
        idx = self._scenario_selected()
        if idx is None:
            return
        self._start_run([self._scenarios[idx]])

    def _run_all(self):
        self._start_run(list(self._scenarios))

    def _start_run(self, scenarios):
        with self._session_lock:
            session = self._session
        if not session:
            messagebox.showerror("Coordinator", "Sem ligação ao Zenoh.")
            return
        demo_mode = self._demo_var.get()
        threading.Thread(target=self._run_worker,
                         args=(session, scenarios, demo_mode), daemon=True).start()

    def _run_worker(self, session, scenarios, demo_mode=False):
        def log(msg):
            self.after(0, self._append_log, msg)

        def vehicle_cb(sid, status):
            self.after(0, self._set_vehicle, sid, status)

        try:
            for i, (name, scenario) in enumerate(scenarios):
                active    = scenario.get("active_vehicles", [])
                timeout_s = float(scenario.get("timeout_s", DEFAULT_TIMEOUT_S))
                if demo_mode:
                    timeout_s += DEMO_TIMEOUT_EXTRA_S

                log(f"\n[coordinator] === {name} | active={active} | timeout={timeout_s}s"
                    f"{' [DEMO]' if demo_mode else ''} ===")

                if not active:
                    log("[coordinator] Sem active_vehicles — a saltar.")
                    continue

                self.after(0, self._reset_vehicles, active)

                log("[coordinator] À espera que todos os veículos estejam prontos…")
                wait_for_pool_ready(session, POOL, DEFAULT_TIMEOUT_S, log, vehicle_cb)

                pub_scenario = {**scenario, "demo": True} if demo_mode else scenario
                payload      = json.dumps(pub_scenario).encode()

                def publish(p=payload):
                    log("[coordinator] A publicar cenário…")
                    session.put("coordinator/scenario", p)
                    log(f"[coordinator] À espera de done de {active}…")

                wait_for_done(session, active, timeout_s, publish, log, vehicle_cb)
                log(f"[coordinator] Cenário '{name}' concluído.")

                if i < len(scenarios) - 1:
                    time.sleep(1.0)

            log("\n[coordinator] Todos os cenários concluídos.")
        except Exception as e:
            log(f"[coordinator] Erro durante execução: {e}")


if __name__ == "__main__":
    app = CoordinatorApp()
    app.mainloop()
