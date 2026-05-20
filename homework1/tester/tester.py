import subprocess
import threading
import time
import matplotlib.pyplot as plt
import json
import os
import sys

# =========================
# GLOBAL PARAMETERS
# =========================
C_LINK = 100.0   # Mbps
RTT = 0.005      # s
ALPHA = 1.5
BASE_PORT = 5000

# Global mapping to synchronize colors across all plots
PROC_COLORS = {
    "blue": "tab:blue",
    "green": "tab:green",
    "red": "tab:red",
    "yellow": "orange",
    "orange": "orange",
    "purple": "tab:purple",
    "cyan": "tab:cyan",
    "magenta": "tab:pink"
}

# =========================
# DEFAULT SCENARIO CONFIG
# =========================
DEFAULT_TRAININGS = [
    {
        "name": "blue",
        "senders": ["w1","w2","w3","w4","w5","w6","w7","w8","w9","w10"],
        "collector": "c1",
        "collector_ip": "10.0.1.1",
        "D": 50,
        "T": 30,
        "phi": 1,
        "cycles": 4
    },
    {
        "name": "green",
        "senders": ["w11","w12","w13","w14","w15","w16","w17","w18"],
        "collector": "c2",
        "collector_ip": "10.0.1.2",
        "D": 62.5,
        "T": 40,
        "phi": 2.5,
        "cycles": 4
    },
    {
        "name": "red",
        "senders": ["w19","w20","w21","w22","w23","w24"],
        "collector": "c3",
        "collector_ip": "10.0.1.3",
        "D": 83.35,
        "T": 30,
        "phi": 4,
        "cycles": 4
    },
    {
        "name": "yellow",
        "senders": ["w25","w26","w27","w28"],
        "collector": "c4",
        "collector_ip": "10.0.1.4",
        "D": 125,
        "T": 40,
        "phi": 5,
        "cycles": 4
    }
]

# =========================
# SCENARIO MANAGEMENT
# =========================
def load_scenarios(filename="scenarios.json"):
    scenarios = {
        "default_incast": {
            "description": "Original default scenario",
            "trainings": DEFAULT_TRAININGS
        }
    }
    
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
                if "scenarios" in data:
                    scenarios.update(data["scenarios"])
        except Exception as e:
            print(f"[ERROR] Cannot read {filename}: {e}")
    else:
        print(f"[WARNING] File {filename} not found. Only the default scenario will be loaded.")
        
    return scenarios

def select_scenario(scenarios):
    print("\n" + "="*50)
    print(" TEST SCENARIO SELECTION ")
    print("="*50)
    
    keys = list(scenarios.keys())
    for i, key in enumerate(keys):
        desc = scenarios[key].get("description", "No description available.")
        print(f"[{i}] {key}")
        print(f"    -> {desc}\n")

    while True:
        try:
            choice = input(f"Choose the scenario number to run (0-{len(keys)-1}): ")
            choice = int(choice)
            if 0 <= choice < len(keys):
                selected_key = keys[choice]
                print(f"\nYou selected: {selected_key}")
                return scenarios[selected_key]["trainings"]
            else:
                print("[!] Invalid choice. Please try again.")
        except ValueError:
            print("[!] Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nExiting.")
            sys.exit(0)


# =========================
# UTILS
# =========================
def get_container_map():
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True
    )
    mapping = {}
    for name in result.stdout.strip().split("\n"):
        parts = name.split("_")
        if len(parts) >= 2:
            mapping[parts[-2]] = name
    return mapping


def docker_exec(node, cmd, cmap):
    if node not in cmap:
        print(f"[ERROR] Node {node} not found!")
        return None
    # Now we return Popen so it can be monitored
    return subprocess.Popen(["docker", "exec", cmap[node]] + cmd)


def compute_window_bytes(f_v):
    return int(ALPHA * (f_v * 1e6) * RTT / 8)


def get_worker_port(worker):
    return BASE_PORT + int(worker[1:])


# =========================
# MULTI-PORT SERVER
# =========================
def start_servers(cmap, trainings):
    used_ports = set()
    for cfg in trainings:
        for w in cfg["senders"]:
            port = get_worker_port(w)
            if port not in used_ports:
                print(f"[SERVER] {cfg['collector']}:{port}")
                # The server goes to background internally in iperf with -D
                docker_exec(cfg["collector"], ["iperf3", "-s", "-D", "-p", str(port)], cmap)
                used_ports.add(port)


# =========================
# CLIENT
# =========================
def start_client(worker, target_ip, port, D_mbit, f_v, cmap):
    window = compute_window_bytes(f_v)
    bytes_to_send = int(D_mbit * 1e6 / 8)

    # No '&' at the end: the command is synchronous, concurrency is handled via Popen
    cmd = (
        f"iperf3 -c {target_ip} -p {port} "
        f"-n {bytes_to_send} "
        f"-w {window} "
        f"--set-mss 1460 --no-delay "
        f"> /dev/null 2>&1"
    )

    return docker_exec(worker, ["bash", "-c", cmd], cmap)


# =========================
# RX/TX MONITORS
# =========================
def get_rx(node, cmap):
    r = subprocess.run(
        ["docker", "exec", cmap[node], "cat", "/sys/class/net/eth0/statistics/rx_bytes"],
        capture_output=True, text=True
    )
    return int(r.stdout.strip() or 0)

def monitor_rx(node, cmap, logfile, stop_event):
    prev_bytes = get_rx(node, cmap)
    prev_time = time.time()
    t_start = prev_time
    with open(logfile, "w") as f:
        f.write("time throughput_mbps\n")
        while not stop_event.is_set():
            time.sleep(1)
            curr_time = time.time()
            curr_bytes = get_rx(node, cmap)
            delta_t = curr_time - prev_time
            if delta_t > 0:
                thr = (curr_bytes - prev_bytes) * 8 / (1e6 * delta_t)
                t_plot = curr_time - t_start
                f.write(f"{t_plot:.1f} {thr:.2f}\n")
                f.flush()
            prev_bytes = curr_bytes
            prev_time = curr_time

def get_tx(node, cmap):
    r = subprocess.run(
        ["docker", "exec", cmap[node], "cat", "/sys/class/net/eth0/statistics/tx_bytes"],
        capture_output=True, text=True
    )
    return int(r.stdout.strip() or 0)

def monitor_tx(node, cmap, logfile, stop_event):
    prev = get_tx(node, cmap)
    t = 0
    with open(logfile, "w") as f:
        f.write("time throughput_mbps\n")
        while not stop_event.is_set():
            time.sleep(1)
            curr = get_tx(node, cmap)
            thr = (curr - prev) * 8 / 1e6
            f.write(f"{t} {thr}\n")
            f.flush()
            prev = curr
            t += 1


# =========================
# TRAINING LOGIC
# =========================
def run_training(cfg, cmap):
    name = cfg["name"]

    print(f"[{name}] Waiting {cfg['phi']}s")
    time.sleep(cfg["phi"])

    K = len(cfg["senders"])
    f_v = C_LINK / K
    
    # Theoretical baseline calculation: (D * K) / C_LINK
    baseline_time = (cfg["D"] * K) / C_LINK
    fct_file = f"{name}_fct.txt"

    print(f"[{name}] START | K={K}, fv={f_v:.2f} Mbps, Baseline: {baseline_time:.2f}s")

    with open(fct_file, "w") as f:
        f.write("cycle actual_fct baseline\n")
        
        for i in range(cfg["cycles"]):
            cycle_start = time.time()
            processes = []

            # 1. Start the clients (they start almost simultaneously)
            for w in cfg["senders"]:
                port = get_worker_port(w)
                p = start_client(w, cfg["collector_ip"], port, cfg["D"], f_v, cmap)
                if p:
                    processes.append(p)

            # 2. Wait for all of them to complete transmission for this cycle
            for p in processes:
                p.wait()

            cycle_end = time.time()
            actual_duration = cycle_end - cycle_start
            
            # Write the data
            f.write(f"{i+1} {actual_duration:.3f} {baseline_time:.3f}\n")
            f.flush()

            print(f"[{name}] Cycle {i+1} COMPLETED | Actual: {actual_duration:.2f}s vs Baseline: {baseline_time:.2f}s")

            # 3. Respect the cycle periodicity T
            time_left = cfg["T"] - actual_duration
            if time_left > 0:
                time.sleep(time_left)
            else:
                print(f"[!] WARNING [{name}]: Cycle {i+1} lasted longer than period T ({cfg['T']}s)!")

    print(f"[{name}] DONE")


# =========================
# PLOT FUNCTIONS
# =========================
def plot_collectors(files, trainings):
    plt.figure()
    
    # Map Collector -> Procedure Name -> Color
    c_to_proc = {cfg["collector"]: cfg["name"] for cfg in trainings}
    
    for c, fname in files.items():
        proc_name = c_to_proc.get(c, "unknown")
        color = PROC_COLORS.get(proc_name, "black")
        
        t, y = [], []
        if os.path.exists(fname):
            with open(fname) as f:
                next(f)
                for line in f:
                    a, b = line.split()
                    t.append(float(a))
                    y.append(float(b))
        plt.plot(t, y, label=f"{c} ({proc_name})", color=color, linewidth=1.5)

    plt.title("Collector RX")
    plt.xlabel("Time (s)")
    plt.ylabel("Mbps")
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.7)


def plot_workers_by_procedure(tx_files, trainings):
    for cfg in trainings:
        proc_name = cfg["name"]
        workers = cfg["senders"]
        
        proc_files = {w: tx_files[w] for w in workers if w in tx_files}
        if not proc_files:
            continue
            
        n = len(proc_files)
        fig, axes = plt.subplots(n, 1, figsize=(10, 2.5 * n), sharex=True)
        if n == 1:
            axes = [axes]

        plot_color = PROC_COLORS.get(proc_name, "tab:blue")

        for ax, (label, fname) in zip(axes, proc_files.items()):
            t, y = [], []
            if os.path.exists(fname):
                with open(fname) as f:
                    next(f)
                    for line in f:
                        a, b = line.split()
                        t.append(float(a))
                        y.append(float(b))

            ax.plot(t, y, color=plot_color, linewidth=1.5)
            ax.margins(y=0.15)
            ax.set_title(f"Worker: {label}", fontsize=10, loc='left', pad=5)
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.set_ylabel("Mbps")

        axes[-1].set_xlabel("Time (s)")
        fig.suptitle(f"Worker TX - Procedure: {proc_name.upper()}", fontsize=14, fontweight='bold')
        plt.tight_layout(rect=[0, 0, 1, 0.96])


def plot_fct(trainings):
    # A single figure organized in subplots based on the number of procedures
    n = len(trainings)
    fig, axes = plt.subplots(n, 1, figsize=(8, 3.5 * n), sharex=True)
    if n == 1:
        axes = [axes]

    for ax, cfg in zip(axes, trainings):
        name = cfg["name"]
        fname = f"{name}_fct.txt"
        color = PROC_COLORS.get(name, "tab:blue")

        cycles, actuals, baselines = [], [], []
        if os.path.exists(fname):
            with open(fname) as f:
                next(f)
                for line in f:
                    c_id, act, base = line.split()
                    cycles.append(int(c_id))
                    actuals.append(float(act))
                    baselines.append(float(base))

        if cycles:
            # Create the bars for Actual FCT
            ax.bar(cycles, actuals, color=color, alpha=0.7, label='Actual FCT')
            
            # Draw the dashed line for the baseline
            baseline_val = baselines[0]
            ax.axhline(y=baseline_val, color='red', linestyle='--', linewidth=2, 
                       label=f'Baseline FCT ({baseline_val:.2f}s)')
            
            # Formatting
            ax.set_title(f"Flow Completion Time - Procedure: {name.upper()}", fontsize=11, loc='left')
            ax.set_ylabel("Time (s)")
            ax.set_xticks(cycles)
            ax.grid(True, linestyle='--', alpha=0.5, axis='y')
            ax.legend(loc='upper right')

    axes[-1].set_xlabel("Cycle Number")
    fig.suptitle("FCT (Flow Completion Time) vs Baseline per Procedure", fontsize=14, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])


# =========================
# MAIN
# =========================
def main():
    scenarios = load_scenarios()
    trainings = select_scenario(scenarios)

    print("\n=== START ===\n")
    cmap = get_container_map()
    
    print("\nStarting servers...")
    start_servers(cmap, trainings)
    time.sleep(2)

    stop_event = threading.Event()
    monitors = []

    # RX MONITOR (collector)
    rx_files = {}
    collectors = set(cfg["collector"] for cfg in trainings)
    for c in collectors:
        fname = f"{c}_rx.txt"
        rx_files[c] = fname
        t = threading.Thread(target=monitor_rx, args=(c, cmap, fname, stop_event))
        t.start()
        monitors.append(t)

    # TX MONITOR (workers)
    tx_files = {}
    workers = set()
    for cfg in trainings:
        workers.update(cfg["senders"])
    for w in workers:
        fname = f"{w}_tx.txt"
        tx_files[w] = fname
        t = threading.Thread(target=monitor_tx, args=(w, cmap, fname, stop_event))
        t.start()
        monitors.append(t)

    # TRAFFIC
    print("\nStarting traffic...")
    threads = []
    for cfg in trainings:
        t = threading.Thread(target=run_training, args=(cfg, cmap))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # Terminate monitoring
    stop_event.set()
    for t in monitors:
        t.join()

    print("\nPlotting...")
    
    plot_collectors(rx_files, trainings)
    plot_workers_by_procedure(tx_files, trainings)
    plot_fct(trainings)  
    
    # This command blocks execution until you close the plot windows
    plt.show()

    # =========================
    # CLEANUP TEMPORARY FILES
    # =========================
    print("\nCleaning up temporary files...")
    
    # Gather all paths of the created files into a single list
    files_to_remove = list(rx_files.values()) + list(tx_files.values())
    for cfg in trainings:
        files_to_remove.append(f"{cfg['name']}_fct.txt")
        
    # Remove files safely
    for f in files_to_remove:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception as e:
            print(f"[WARNING] Could not remove {f}: {e}")

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()