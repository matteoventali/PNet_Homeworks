#!/bin/bash
# =========================================================================
# Experiment 1: 5-Pane Tmux Layout
# Top row: h1 | h3 | sf1
# Bottom row: sf3 | sf2
# =========================================================================

SESSION_NAME="exp1"

# Check if the session already exists to prevent nested or duplicate sessions
tmux has-session -t $SESSION_NAME 2>/dev/null
if [ $? == 0 ]; then
    echo "Session $SESSION_NAME already exists. Attaching to it."
    tmux attach-session -t $SESSION_NAME
    exit 0
fi

# 1. Create a new detached session and start the top-left pane (h1)
tmux new-session -d -s $SESSION_NAME "kathara connect h1"

# 2. Split the window vertically to create the bottom row. 
# The new pane (bottom-left) gets 50% of the screen height.
tmux split-window -v -p 50 "kathara connect sf3"

# 3. Split the bottom row horizontally to create the bottom-right pane.
tmux split-window -h -p 50 "kathara connect sf2"

# 4. Select the top row (Pane index 0)
tmux select-pane -t 0

# 5. Split the top row horizontally. 
# The new pane gets 66% of the width, leaving 33% for the left pane.
tmux split-window -h -p 66 "kathara connect h3"

# 6. Split the newly created right pane (which is 66% wide) in half.
# This results in three perfectly equal columns on the top row (33% each).
tmux split-window -h -p 50 "kathara connect sf1"

# 7. Attach to the configured session
tmux attach-session -t $SESSION_NAME
