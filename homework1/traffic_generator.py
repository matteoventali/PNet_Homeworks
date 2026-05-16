import subprocess
import threading
import time
import os
import matplotlib.pyplot as plt

# =========================
# PARAMETRI GLOBALI
# =========================
C_LINK = 100.0   # Mbps
RTT = 0.005     # s
ALPHA = 1.5
BASE_PORT = 5000

# =========================
# CONFIG TRAINING
# =========================
TRAININGS = [
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
# SERVER MULTI-PORT
# =========================
def start_servers(cmap):
    used_ports = set()

    for cfg in TRAININGS:
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
# MONITOR RX
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
    prev = get_rx(node, cmap)
    t = 0

    with open(logfile, "w") as f:
        f.write("time throughput_mbps\n")

        while not stop_event.is_set():
            time.sleep(1)
            curr = get_rx(node, cmap)
            thr = (curr - prev) * 8 / 1e6
            f.write(f"{t} {thr}\n")
            f.flush()
            prev = curr
            t += 1


# =========================
# MONITOR TX
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


# =====================================================================
# PLOT FUNCTIONS (Mantenute le originali + Aggiunte le avanzate distinte)
# =====================================================================
def plot_collectors(files):
    plt.figure()
    for label, fname in files.items():
        t, y = [], []
        if not os.path.exists(fname): continue
        with open(fname) as f:
            next(f)
            for line in f:
                a, b = line.split()
                t.append(float(a))
                y.append(float(b))
        plt.plot(t, y, label=label)

    plt.title("Collector RX (Original)")
    plt.xlabel("Time (s)")
    plt.ylabel("Mbps")
    plt.legend()
    plt.grid(True)
    plt.show()


def plot_workers(files):
    n = len(files)
    fig, axes = plt.subplots(n, 1, figsize=(8, 3*n), sharex=True)

    if n == 1:
        axes = [axes]

    for ax, (label, fname) in zip(axes, files.items()):
        t, y = [], []
        if not os.path.exists(fname): continue
        with open(fname) as f:
            next(f)
            for line in f:
                a, b = line.split()
                t.append(float(a))
                y.append(float(b))

        ax.plot(t, y)
        ax.set_title(label)
        ax.grid(True)

    axes[-1].set_xlabel("Time (s)")
    fig.suptitle("Worker TX (Original)")
    plt.show()


def read_monitor_file(fname):
    t_data, y_data = [], []
    if not os.path.exists(fname): return t_data, y_data
    with open(fname, "r") as f:
        next(f)
        for line in f:
            parts = line.strip().split()
            if len(parts) == 2:
                t_data.append(float(parts[0]))
                y_data.append(float(parts[1]))
    return t_data, y_data


def plot_advanced_metrics_distinct(rx_files, tx_files):
    print("\nGenerating advanced distinct plots for the thesis...")
    plt.style.use('seaborn-v0_8-whitegrid')
    plt.rcParams.update({'font.size': 11, 'figure.dpi': 150})
    cmap_colors = {"blue": "tab:blue", "green": "tab:green", "red": "tab:red", "yellow": "orange"}

    # Caricamento e aggregazione dati comuni
    global_rx, global_tx = {}, {}
    worker_stats = {}
    
    for c, c_name in rx_files.items():
        t_data, mbps_data = read_monitor_file(c_name)
        if not t_data: continue
        for t, val in zip(t_data, mbps_data):
            sec = int(round(t - t_data[0]))
            global_rx[sec] = global_rx.get(sec, 0.0) + val

    for w, c_name in tx_files.items():
        t_data, mbps_data = read_monitor_file(c_name)
        if not t_data: continue
        worker_stats[w] = []
        for t, val in zip(t_data, mbps_data):
            sec = int(round(t - t_data[0]))
            global_tx[sec] = global_tx.get(sec, 0.0) + val
            if val > 1.0: worker_stats[w].append(val)

    max_sec = max(max(global_tx.keys(), default=0), max(global_rx.keys(), default=0))
    time_sec = list(range(max_sec + 1))

    # --- ADVANCED PLOT 1: ENHANCED COLLECTOR RX WITH TOTAL CAPACITY ---
    plt.figure(figsize=(11, 5))
    max_rx = 0
    for c, c_name in rx_files.items():
        t_data, mbps_data = read_monitor_file(c_name)
        if not t_data: continue
        t_data = [t - t_data[0] for t in t_data]
        color = "black"
        for cfg in TRAININGS:
            if cfg["collector"] == c:
                color = cmap_colors.get(cfg["name"], "black")
                break
        plt.plot(t_data, mbps_data, label=f"Collector {c} ({cfg['name']})", color=color, linewidth=2)
        plt.fill_between(t_data, 0, mbps_data, color=color, alpha=0.1)
        if max(mbps_data) > max_rx: max_rx = max(mbps_data)

    # Linea della banda aggregata totale di rete
    t_tot = sorted(global_rx.keys())
    val_tot = [global_rx[t] for t in t_tot]
    plt.plot(t_tot, val_tot, label="Total Core Fabric Throughput", color="black", linestyle="--", linewidth=2.2)
    
    plt.title("Application-Level Throughput at Collectors (RX)")
    plt.xlabel("Simulation Time (s)")
    plt.ylabel("Throughput (Mbps)")
    plt.ylim(0, max(max_rx * 1.2, 250))
    plt.legend(loc="upper right", frameon=True)
    plt.savefig("thesis_collector_rx.png", bbox_inches='tight')
    plt.show()

    # --- ADVANCED PLOT 2: AGGREGATED WORKER TX ---
    plt.figure(figsize=(11, 5))
    max_tx = 0
    total_tx_by_color = {cfg["name"]: {} for cfg in TRAININGS}
    for w, c_name in tx_files.items():
        t_data, mbps_data = read_monitor_file(c_name)
        if not t_data: continue
        color_name = "blue"
        for cfg in TRAININGS:
            if w in cfg["senders"]: color_name = cfg["name"]; break
        for i in range(len(t_data)):
            sec = int(round(t_data[i] - t_data[0]))
            total_tx_by_color[color_name][sec] = total_tx_by_color[color_name].get(sec, 0.0) + mbps_data[i]

    for cfg in TRAININGS:
        c_name = cfg["name"]
        color = cmap_colors.get(c_name, "black")
        data_dict = total_tx_by_color[c_name]
        if not data_dict: continue
        t_list = sorted(data_dict.keys())
        mbps_list = [data_dict[k] for k in t_list]
        plt.plot(t_list, mbps_list, label=f"Workers {c_name} (Aggregated)", color=color, linewidth=2)
        plt.fill_between(t_list, 0, mbps_list, color=color, alpha=0.1)
        if max(mbps_list) > max_tx: max_tx = max(mbps_list)

    plt.title("Aggregated Egress Traffic Generated by Workers (TX)")
    plt.xlabel("Simulation Time (s)")
    plt.ylabel("Throughput (Mbps)")
    plt.ylim(0, max_tx * 1.15)
    plt.xlim(0, 100)
    plt.legend(loc="upper right", frameon=True)
    plt.savefig("thesis_worker_tx.png", bbox_inches='tight')
    plt.show()

    # --- ADVANCED PLOT 3: NETWORK IN-FLIGHT DATA (QUEUEING) ---
    plt.figure(figsize=(11, 5))
    in_flight_mb = []
    current_buffered = 0.0
    for s in time_sec:
        delta = global_tx.get(s, 0.0) - global_rx.get(s, 0.0)
        current_buffered += delta
        if current_buffered < 0: current_buffered = 0 
        in_flight_mb.append(current_buffered)

    plt.plot(time_sec, in_flight_mb, color='purple', linewidth=2.5, label="In-Flight Bytes")
    plt.fill_between(time_sec, 0, in_flight_mb, color='purple', alpha=0.2)
    plt.title("Network In-Flight Data (Aggregated Switch Buffer Occupancy)")
    plt.xlabel("Simulation Time (s)")
    plt.ylabel("Data Volume in Queues (Mb)")
    plt.xlim(0, min(max_sec, 100))
    plt.legend(loc="upper right")
    plt.savefig("thesis_inflight_data.png", bbox_inches='tight')
    plt.show()

    # --- ADVANCED PLOT 4: MAX-MIN FAIRNESS (BLUE PROCEDURE) ---
    blue_cfg = TRAININGS[0]
    blue_workers = blue_cfg["senders"]
    avg_throughput, labels = [], []
    for w in blue_workers:
        if w in worker_stats and len(worker_stats[w]) > 0:
            avg_throughput.append(sum(worker_stats[w]) / len(worker_stats[w]))
            labels.append(w)
            
    if avg_throughput:
        plt.figure(figsize=(11, 5))
        x_pos = range(len(labels))
        plt.bar(x_pos, avg_throughput, color=cmap_colors.get(blue_cfg['name'], 'blue'), alpha=0.7, edgecolor='black', label="Worker Avg Rate")
        plt.xticks(x_pos, labels)
        ideal_share = C_LINK / len(blue_workers)
        plt.axhline(y=ideal_share, color='red', linestyle='--', linewidth=2, label=f"Ideal Max-Min Share ({ideal_share:.1f} Mbps)")
        plt.title(f"Transmission Fairness Evaluation (Procedure: {blue_cfg['name'].upper()})")
        plt.xlabel("Active Senders (Workers)")
        plt.ylabel("Mean Throughput in Active State (Mbps)")
        plt.ylim(0, max(ideal_share * 1.5, max(avg_throughput, default=0) * 1.2))
        plt.legend(loc="upper right", frameon=True)
        plt.savefig("thesis_fairness.png", bbox_inches='tight')
        plt.show()


# =========================
# MAIN
# =========================
def main():
    print("\n=== START ===\n")

    cmap = get_container_map()

    for k, v in cmap.items():
        print(k, "->", v)

    print("\nStarting servers...")
    start_servers(cmap)

    time.sleep(2)

    stop_event = threading.Event()
    monitors = []

    # =========================
    # RX MONITOR (collector)
    # =========================
    rx_files = {}
    collectors = set(cfg["collector"] for cfg in TRAININGS)

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
    for cfg in TRAININGS:
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

    for cfg in TRAININGS:
        t = threading.Thread(target=run_training, args=(cfg, cmap))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    stop_event.set()

    for t in monitors:
        t.join()

    # Mostra i tuoi grafici originali
    print("\nPlotting original configurations...")
    plot_collectors(rx_files)
    plot_workers(tx_files)

    # Mostra e salva i nuovi grafici avanzati distinti
    plot_advanced_metrics_distinct(rx_files, tx_files)

    # =========================
    # CLEANUP FILE TEMPORANEI
    # =========================
    print("\nCleaning up temporary log files (.txt) from disk...")
    for fname in list(rx_files.values()) + list(tx_files.values()):
        if os.path.exists(fname):
            try:
                os.remove(fname)
            except Exception as e:
                print(f"[WARNING] Could not remove {fname}: {e}")

    print("\n=== DONE ===\n")


if __name__ == "__main__":
    main()