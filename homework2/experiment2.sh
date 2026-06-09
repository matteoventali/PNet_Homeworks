#!/bin/bash
# =========================================================================
# Experiment 2: 3-Pane Tmux Layout
# Layout: h2 | h4 | sf3 (Side-by-side columns)
# =========================================================================

SESSION_NAME="exp2"

# Check if the session already exists
tmux has-session -t $SESSION_NAME 2>/dev/null
if [ $? == 0 ]; then
    echo "Session $SESSION_NAME already exists. Attaching to it."
    tmux attach-session -t $SESSION_NAME
    exit 0
fi

# 1. Create a new detached session and start the left pane (h2)
tmux new-session -d -s $SESSION_NAME "kathara connect h2"

# 2. Split the window horizontally. 
# The new pane gets 66% of the width.
tmux split-window -h -p 66 "kathara connect h4"

# 3. Split the right pane horizontally in half.
tmux split-window -h -p 50 "kathara connect sf3"

# 4. Enforce an even horizontal layout to ensure columns are exactly equal
tmux select-layout even-horizontal

# 5. Select the first pane (h2) to start with the cursor on the left
tmux select-pane -t 0

# 6. Attach to the configured session
tmux attach-session -t $SESSION_NAME
