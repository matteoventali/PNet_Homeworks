from pox.core import core
import pox.openflow.libopenflow_01 as of
import logging

log = core.getLogger()

class IncastOptimizer(object):
    def __init__(self):
        # Ascolta l'evento dal Controller
        core.IncastController.addListenerByName("OptimizationRequired", self._handle_OptimizationRequired)
        self.C_LINK = 100.0 
        log.info("[OPTIMIZER] Flow-Splitting Optimizer avviato.")

    def _handle_OptimizationRequired(self, event):
        procedures = event.procedures
        ctrl = core.IncastController
        spines = sorted([d for d in ctrl.adjacency.keys() if len(ctrl.edge_ports.get(d, set())) == 0])
        
        if not spines or len(spines) < 2:
            log.warning("[OPTIMIZER] Topologia non valida o Spine insufficienti.")
            return

        # 1. Preparazione dei dati delle procedure attive
        active_list = []
        for ip, proc in procedures.items():
            if proc['round_number'] < 2: continue
            active_list.append({'ip': ip, 'proc': proc, 'weight': len(proc['workers']) * proc['Dv']})

        if not active_list: return

        # Ordiniamo le procedure per peso (Greedy)
        active_list.sort(key=lambda x: x['weight'], reverse=True)

        # 2. Allocazione Granulare per Worker
        # Struttura: { (worker_ip, collector_ip): spine_dpid }
        flow_mapping = {}
        spine_loads = {s: 0.0 for s in spines}

        log.info("\n[OPTIMIZER] --- RICALCOLO FLOW-SPLITTING ---")

        for entry in active_list:
            collector_ip = entry['ip']
            proc = entry['proc']
            # Ordiniamo i worker per IP per rendere l'assegnazione deterministica
            workers = sorted(list(proc['workers']))
            
            log.info("[OPTIMIZER] Procedura %s (%d worker): Distribuzione flussi...", collector_ip, len(workers))
            
            for w_ip in workers:
                # Scegliamo lo Spine meno carico per questo specifico worker
                chosen_spine = min(spine_loads, key=spine_loads.get)
                flow_mapping[(w_ip, collector_ip)] = chosen_spine
                
                # Incrementiamo il carico dello Spine con la quota parte del worker (Dv)
                spine_loads[chosen_spine] += proc['Dv']
                
        # 3. Installazione Regole OpenFlow (Priorità 100)
        self._deploy_flow_rules(flow_mapping, ctrl)
        
        log.info("[OPTIMIZER] Carico stimato sugli Spine: %s", 
                 {f"S{k}": f"{v:.1f}Mb" for k, v in spine_loads.items()})

    def _deploy_flow_rules(self, flow_mapping, ctrl):
        ip_to_mac = {ip: mac for mac, ip in ctrl.mac_to_ip.items()}
        count = 0
        
        for (w_ip, c_ip), spine_dpid in flow_mapping.items():
            w_mac = ip_to_mac.get(w_ip)
            if not w_mac: continue
            
            # Identifichiamo il Leaf switch del worker
            leaf_dpid, _ = ctrl.mac_to_location.get(w_mac, (None, None))
            if not leaf_dpid: continue
            
            # Porta verso lo Spine scelto
            out_port = ctrl.adjacency.get(leaf_dpid, {}).get(spine_dpid)
            
            if out_port:
                self._install_high_priority_rule(leaf_dpid, w_ip, c_ip, out_port)
                count += 1
        
        log.info("[OPTIMIZER] Installate %d regole di flusso granulari.\n", count)

    def _install_high_priority_rule(self, dpid, src, dst, port):
        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.match.dl_type = 0x0800
        msg.match.nw_src = src
        msg.match.nw_dst = dst
        msg.actions.append(of.ofp_action_output(port=port))
        
        conn = core.openflow.getConnection(dpid)
        if conn: conn.send(msg)

def launch():
    core.registerNew(IncastOptimizer)