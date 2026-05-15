from pox.core import core
import pox.openflow.libopenflow_01 as of
import logging

log = core.getLogger()

class IncastOptimizer(object):
    def __init__(self):
        # Listening the flow installation event
        core.IncastController.addListenerByName("OptimizationRequired", self._handle_OptimizationRequired)
        self.C_LINK = 100.0 
        log.info("[OPTIMIZER] Flow-Splitting Optimizer started.")

    def _handle_OptimizationRequired(self, event):
        # Extracting the procedures features from the event received
        procedures = event.procedures
        
        # Computing the set of spine switches
        ctrl = core.IncastController
        spines = sorted([d for d in ctrl.adjacency.keys() if len(ctrl.edge_ports.get(d, set())) == 0])
        
        if not spines or len(spines) < 2:
            log.warning("[OPTIMIZER] Invalid topology. Missing spines")
            return

        # Computing the features of active procedures (procedures that have already reached the second round)
        active_list = []
        for ip, proc in procedures.items():
            if proc['round_number'] < 2: continue
            active_list.append({'ip': ip, 'proc': proc, 'weight': len(proc['workers']) * proc['Dv']})
        if not active_list: return

        # Sorting the procedures according the weight (K_v * D_v)
        active_list.sort(key=lambda x: x['weight'], reverse=True)

        flow_mapping = {} # (worker_ip, collector_ip) -> spine_dpid
        spine_loads = {s: 0.0 for s in spines}

        log.info("\n[OPTIMIZER] --- COMPUTING NEW RULES ---")

        for entry in active_list:
            collector_ip = entry['ip']
            proc = entry['proc']
            workers = sorted(list(proc['workers']))
            
            log.info("[OPTIMIZER] Procedura %s (%d worker): Distribuzione flussi...", collector_ip, len(workers))
            
            for w_ip in workers:
                # Choosing the spine with smallest load
                chosen_spine = min(spine_loads, key=spine_loads.get)
                flow_mapping[(w_ip, collector_ip)] = chosen_spine
                
                # Updating the spine_loads
                spine_loads[chosen_spine] += proc['Dv']
                
        # Installing the new rules
        self._deploy_flow_rules(flow_mapping, ctrl)
        
        log.info("[OPTIMIZER] Estimated loading: %s", 
                 {f"S{k}": f"{v:.1f}Mb" for k, v in spine_loads.items()})

    def _deploy_flow_rules(self, flow_mapping, ctrl):
        ip_to_mac = {ip: mac for mac, ip in ctrl.mac_to_ip.items()}
        
        for (w_ip, c_ip), spine_dpid in flow_mapping.items():
            w_mac = ip_to_mac.get(w_ip)
            if not w_mac: continue
            
            # Identifying position of worker and collector
            leaf_dpid, _ = ctrl.mac_to_location.get(w_mac, (None, None))
            collector_dpid = ctrl.collector_dpid_map.get(c_ip)
            
            if not leaf_dpid or not collector_dpid: continue

            # If the traffic is local we must not set any route trough the spine
            if leaf_dpid == collector_dpid:
                continue

            path_selected = [leaf_dpid, spine_dpid, collector_dpid]
            
            # Route between ingress leaf switch to spine switch
            out_port_leaf = ctrl.adjacency.get(leaf_dpid, {}).get(spine_dpid)
            if out_port_leaf:
                self._install_high_priority_rule(leaf_dpid, w_ip, c_ip, out_port_leaf)
                
            # Route between spine switch to egress switch
            out_port_spine = ctrl.adjacency.get(spine_dpid, {}).get(collector_dpid)
            if out_port_spine:
                self._install_high_priority_rule(spine_dpid, w_ip, c_ip, out_port_spine)
                
            log.info("[OPTIMIZER] Installed path for (%s, %s): %s", w_ip, c_ip, path_selected)

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