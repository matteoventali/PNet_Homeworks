#!/bin/bash

# Handling optimizer
OPT_MODULE="optimizer"

if [ "$1" == "advanced" ]; then
    OPT_MODULE="advanced_optimizer"
    echo "[INFO] Modality: ADVANCED OPTIMIZER"
elif [ "$1" == "standard" ] || [ -z "$1" ]; then
    OPT_MODULE="optimizer"
    echo "[INFO] Modality: STANDARD OPTIMIZER (Default)"
else
    echo "[ERROR] Parameter '$1' unknown."
    echo "Correctly use: $0 [standard | advanced]"
    exit 1
fi

echo "[INFO] Cleaning up environment and old logs..."
cd /shared
rm -f discovery.log telemetry.log state.log pox_main.log
touch discovery.log telemetry.log state.log
cd /

SESSION="sdn_lab"

# Close previous sessions if the script is accidentally run twice
tmux kill-session -t $SESSION 2>/dev/null

echo "[INFO] Creating Tmux interface and starting the Controller..."

# Create the session in the background
tmux new-session -d -s $SESSION

# 2. Create the 4 panes without sending commands yet
tmux split-window -h      # Split horizontally (creates Pane 1 on the right)
tmux split-window -v      # Split the right pane vertically (creates Pane 2 on the bottom right)
tmux select-pane -t 0     # Return to the first pane on the left
tmux split-window -v      # Split the left pane vertically (creates Pane 3 on the bottom left)

# 3. Force a perfect grid layout
tmux select-layout -t $SESSION:0 tiled

# 4. Wait 1 second to ensure all Bash shells are ready to read input
sleep 1

# 5. Inject the commands. Now indices 0, 1, 2, and 3 are stable.
# Top Left:
tmux send-keys -t $SESSION:0.0 '/pox/pox.py openflow.of_01 -port=6653 openflow.discovery controller $OPT_MODULE | tee pox_main.log' C-m
# Bottom Left:
tmux send-keys -t $SESSION:0.1 'tail -f /shared/discovery.log' C-m
# Top Right:
# tmux send-keys -t $SESSION:0.2 'tail -f /shared/telemetry.log' C-m
tmux send-keys -t $SESSION:0.2 'watch -n 1 -t cat /shared/telemetry.log' C-m
# Bottom Right:
tmux send-keys -t $SESSION:0.3 'tail -f /shared/state.log' C-m

echo "[SUCCESS] Entering the command console..."
tmux attach-session -t $SESSION