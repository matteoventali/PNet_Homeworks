#!/bin/bash

echo "[INFO] Pulizia dell'ambiente e vecchi log..."
rm -f discovery.log telemetry.log state.log pox_main.log
touch discovery.log telemetry.log state.log

SESSION="sdn_lab"

# Chiude sessioni precedenti se il file viene lanciato due volte per sbaglio
tmux kill-session -t $SESSION 2>/dev/null

echo "[INFO] Creazione dell'interfaccia Tmux e avvio del Controller..."

# Crea la sessione in background
tmux new-session -d -s $SESSION

# 2. Crea i 4 riquadri senza inviare ancora comandi
tmux split-window -h      # Divide a metà (crea Pannello 1 a destra)
tmux split-window -v      # Divide quello di destra (crea Pannello 2 in basso a destra)
tmux select-pane -t 0     # Torna al primo a sinistra
tmux split-window -v      # Divide quello di sinistra (crea Pannello 3 in basso a sinistra)

# 3. Forza la visualizzazione a griglia perfetta
tmux select-layout -t $SESSION:0 tiled

# 4. Aspetta 1 secondo per assicurarsi che tutte le shell Bash siano pronte a leggere i tasti
sleep 1

# 5. Inietta i comandi. Ora gli indici 0,1,2,3 sono stabili.
# Alto Sinistra:
tmux send-keys -t $SESSION:0.0 '/pox/pox.py openflow.of_01 -port=6653 openflow.discovery controller optimizer | tee pox_main.log' C-m
# Basso Sinistra:
tmux send-keys -t $SESSION:0.1 'tail -f discovery.log' C-m
# Alto Destra:
tmux send-keys -t $SESSION:0.2 'tail -f telemetry.log' C-m
# Basso Destra:
tmux send-keys -t $SESSION:0.3 'tail -f state.log' C-m

echo "[SUCCESS] Entro nella console di comando..."
tmux attach-session -t $SESSION