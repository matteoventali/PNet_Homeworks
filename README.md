# PNet Homeworks

This repository contains the homework projects for the PNet course.

## Homework 1: SDN Networking Controller
Implementation of an SDN (Software Defined Networking) controller.

### How to launch the experiment
The network topology is managed using Kathara. To start the experiment, navigate to the `homework1` directory and start the lab:

```bash
cd homework1
kathara lstart
```

Once the lab is running, connect to the `controller` node to start the SDN controller:

```bash
kathara connect controller
```

### Controller Launch Modes
Inside the controller, you can use the `start_controller.sh` script to launch the controller along with the desired optimization module. The script creates a `tmux` session with multiple panes to easily monitor discovery, telemetry, and state logs.

Run the script by passing one of the following modalities:

```bash
bash start_controller.sh [modality]
```

**Available modalities:**
- **`no parameter`**: Starts the controller without loading any specific optimizer module (controller implements ECMP strategy by default).
- **`advanced`**: Starts the controller with the Advanced Optimizer (`advanced_optimizer`).
- **`normal`**: Starts the controller with the Standard Optimizer (`optimizer`).
- **`temporal`**: Starts the controller with the Predictive/Temporal Optimizer (`predictive_optimizer`).
- **`dumb`**: Starts a basic controller using standard POX modules (`forwarding.l2_learning` and `openflow.spanning_tree`) without custom optimizations.


### Traffic Generation and Scenarios

To simulate network traffic and evaluate the controller's performance, you can use the `tester.py` script. This script generates incast traffic patterns from multiple senders to a single collector, based on predefined scenarios.

To run the `tester.py` script, navigate to the `homework1/tester` directory and execute it:

```bash
cd homework1/tester
python3 tester.py
```

The script will first prompt you to select a scenario.

**Available Scenarios:**

The `tester.py` script comes with a default incast scenario, which includes four distinct traffic procedures, each characterized by a different number of senders, data volume, and periodicity:

-   **Blue**: 10 senders, 50 MB data volume, 30s period, 1s initial delay.
-   **Green**: 8 senders, 62.5 MB data volume, 40s period, 2.5s initial delay.
-   **Red**: 6 senders, 83.35 MB data volume, 30s period, 4s initial delay.
-   **Yellow**: 4 senders, 125 MB data volume, 40s period, 5s initial delay.

These procedures are designed to create varying levels of incast congestion.

**Custom Scenarios:**

You can define and load custom scenarios by creating a `scenarios.json` file in the same directory as `tester.py`. This file should contain a dictionary of scenarios, where each key is a scenario name and its value is an object containing a `description` and a `trainings` list, following the structure of the `DEFAULT_TRAININGS` in `tester.py`. The script will automatically detect and list these custom scenarios for selection.

After traffic generation, the script will display plots for collector RX throughput, worker TX throughput, and Flow Completion Time (FCT) for each procedure, allowing for visual analysis of the experiment results.


To terminate the experiment and clean up the lab environment, exit the controller and run:
```bash
kathara lclean
```

## Homework 2: TBD
The content of this homework will be defined later.

---

### Authors
- Matteo Ventali (1985026)
- Serena Ragaglia (1941007)