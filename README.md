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