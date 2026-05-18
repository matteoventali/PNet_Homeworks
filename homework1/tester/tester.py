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

# =========================
# DEFAULT SCENARIO CONFIG
# (From incast_generator.py)
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
            "description": "Original default scenario (from incast_generator.py)",
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
        return
    subprocess.Popen(["docker", "exec", cmap[node]] + cmd)


def compute_window_bytes(f_v):
    return int(ALPHA * (f_v * 1e6) * RTT / 8)


def get_worker_port(worker):
    return BASE_PORT + int(worker[1:])  # w1 → 5001


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
                docker_exec(cfg["collector"],
                            ["iperf3", "-s", "-D", "-p", str(port)],
                            cmap)
                used_ports.add(port)


# =========================
# CLIENT
# =========================
def start_client(worker, target_ip, port, D_mbit, f_v, cmap):
    window = compute_window_bytes(f_v)
    bytes_to_send = int(D_mbit * 1e6 / 8)

    print(f"[FLOW] {worker} -> {target_ip}:{port} | fv={f_v:.2f}")

    cmd = (
        f"iperf3 -c {target_ip} -p {port} "
        f"-n {bytes_to_send} "
        f"-w {window} "
        f"--set-mss 1460 --no-delay "
        f"> /dev/null 2>&1 &"
    )

    docker_exec(worker, ["bash", "-c", cmd], cmap)


# =========================
# RX MONITOR
# =========================
def get_rx(node, cmap):
    r = subprocess.run(
        ["docker", "exec", cmap[node],
         "cat", "/sys/class/net/eth0/statistics/rx_bytes"],
        capture_output=True, text=True
    )
    return int(r.stdout.strip() or 0)


def monitor_rx(node, cmap, logfile, stop_event):
    print(f"[MONITOR RX] {node}")
    prev_bytes = get_rx(node, cmap)
    prev_time = time.time()
    t_start = prev_time

    with open(logfile, "w") as f:
        f.write("time throughput_mbps\n")

        while not stop_event.is_set():
            time.sleep(1)
            curr_time = time.time()
            curr_bytes = get_rx(node, cmap)
            
            # Exact calculation: Delta Bytes / Delta Real Time
            delta_t = curr_time - prev_time
            if delta_t > 0:
                thr = (curr_bytes - prev_bytes) * 8 / (1e6 * delta_t)
                
                # Round time for the plot
                t_plot = curr_time - t_start
                f.write(f"{t_plot:.1f} {thr:.2f}\n")
                f.flush()
                
            prev_bytes = curr_bytes
            prev_time = curr_time

# =========================
# TX MONITOR
# =========================
def get_tx(node, cmap):
    r = subprocess.run(
        ["docker", "exec", cmap[node],
         "cat", "/sys/class/net/eth0/statistics/tx_bytes"],
        capture_output=True, text=True
    )
    return int(r.stdout.strip() or 0)


def monitor_tx(node, cmap, logfile, stop_event):
    print(f"[MONITOR TX] {node}")
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
# TRAINING
# =========================
def run_training(cfg, cmap):
    name = cfg["name"]

    print(f"[{name}] Waiting {cfg['phi']}s")
    time.sleep(cfg["phi"])

    K = len(cfg["senders"])
    f_v = C_LINK / K

    print(f"[{name}] START | K={K}, fv={f_v:.2f}")

    for i in range(cfg["cycles"]):
        print(f"[{name}] Cycle {i+1}")

        start = time.time()

        for w in cfg["senders"]:
            port = get_worker_port(w)
            start_client(w, cfg["collector_ip"], port,
                         cfg["D"], f_v, cmap)

        time.sleep(max(0, cfg["T"] - (time.time() - start)))

    print(f"[{name}] DONE")


# =========================
# PLOT
# =========================
def plot_collectors(files, trainings):
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # Map to color collector lines based on procedure name
    color_map = {cfg["collector"]: cfg["name"] for cfg in trainings}
    
    for label, fname in files.items():
        t, y = [], []
        with open(fname) as f:
            next(f)
            for line in f:
                a, b = line.split()
                t.append(float(a))
                y.append(float(b))
                
        c_name = color_map.get(label, 'tab:blue')
        # Map yellow to orange for visibility on white background
        plot_color = 'orange' if c_name == 'yellow' else c_name
        
        ax.plot(t, y, label=f"Collector {label} ({c_name})", color=plot_color, linewidth=2)

    # Pad the title so it doesn't touch the graph
    ax.set_title("Collector RX - Throughput per Procedure", fontsize=14, fontweight='bold', pad=20)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Mbps")
    
    # Add vertical margins so data doesn't hit the ceiling
    ax.margins(y=0.15)
    
    # Place legend completely outside the plot to the right
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', borderaxespad=0.)
    ax.grid(True, linestyle='--', alpha=0.7)
    
    # Adjust layout to make room for the external legend
    plt.tight_layout()


def plot_workers_by_procedure(tx_files, trainings):
    for cfg in trainings:
        proc_name = cfg["name"]
        workers = cfg["senders"]
        
        proc_files = {w: tx_files[w] for w in workers if w in tx_files}
        
        if not proc_files:
            continue
            
        n = len(proc_files)
        # Dynamic height based on number of workers
        fig, axes = plt.subplots(n, 1, figsize=(10, 2.5 * n), sharex=True)
        
        if n == 1:
            axes = [axes]

        plot_color = 'orange' if proc_name == 'yellow' else proc_name
        if plot_color not in ['blue', 'green', 'red', 'orange', 'cyan', 'magenta', 'black', 'purple']:
            plot_color = 'tab:blue'

        for ax, (label, fname) in zip(axes, proc_files.items()):
            t, y = [], []
            with open(fname) as f:
                next(f)
                for line in f:
                    a, b = line.split()
                    t.append(float(a))
                    y.append(float(b))

            ax.plot(t, y, color=plot_color, linewidth=1.5)
            # Add vertical margins to prevent line from hitting the top
            ax.margins(y=0.15)
            
            # Place subplot title cleanly
            ax.set_title(f"Worker: {label}", fontsize=10, loc='left', pad=5)
            ax.grid(True, linestyle='--', alpha=0.7)
            ax.set_ylabel("Mbps")

        axes[-1].set_xlabel("Time (s)")
        
        # Main figure title
        fig.suptitle(f"Worker TX - Procedure: {proc_name.upper()}", fontsize=14, fontweight='bold')
        
        # Use rect to ensure suptitle doesn't overlap with the top subplot
        plt.tight_layout(rect=[0, 0, 1, 0.96])


# =========================
# MAIN
# =========================
def main():
    # 1. Load and select the scenario
    scenarios = load_scenarios()
    trainings = select_scenario(scenarios)

    print("\n=== START ===\n")

    cmap = get_container_map()

    for k, v in cmap.items():
        print(k, "->", v)

    print("\nStarting servers...")
    start_servers(cmap, trainings)

    time.sleep(2)

    stop_event = threading.Event()
    monitors = []

    # =========================
    # RX MONITOR (collector)
    # =========================
    rx_files = {}
    collectors = set(cfg["collector"] for cfg in trainings)

    for c in collectors:
        fname = f"{c}_rx.txt"
        rx_files[c] = fname

        t = threading.Thread(target=monitor_rx,
                             args=(c, cmap, fname, stop_event))
        t.start()
        monitors.append(t)

    # =========================
    # TX MONITOR (workers)
    # =========================
    tx_files = {}
    workers = set()
    for cfg in trainings:
        workers.update(cfg["senders"])

    for w in workers:
        fname = f"{w}_tx.txt"
        tx_files[w] = fname

        t = threading.Thread(target=monitor_tx,
                             args=(w, cmap, fname, stop_event))
        t.start()
        monitors.append(t)

    # =========================
    # TRAFFIC
    # =========================
    print("\nStarting traffic...")
    threads = []

    for cfg in trainings:
        t = threading.Thread(target=run_training, args=(cfg, cmap))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    stop_event.set()

    for t in monitors:
        t.join()

    print("\nPlotting...")
    
    plot_collectors(rx_files, trainings)
    plot_workers_by_procedure(tx_files, trainings)
    
    # Show all generated windows simultaneously
    plt.show()

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()