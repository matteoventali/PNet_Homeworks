from pox.core import core
import pox.openflow.libopenflow_01 as of
import time
import math

log = core.getLogger()

class PredictiveIncastOptimizer(object):
    def __init__(self):
        core.IncastController.addListenerByName("OptimizationRequired", self._handle_OptimizationRequired)
        self.C_LINK = 100.0 
        
        # Persistently save routes so they can be reinstalled or queried.
        # Structure: (worker_ip, collector_ip) -> spine_dpid
        self.global_allocations = {} 
        
        log.info("[PREDICTIVE OPTIMIZER] Graph-Clustering Time-Aware Optimizer started.")

    def _is_predictable(self, proc):
        if proc.get('Dv') is None or proc.get('Dv') <= 0: return False
        if proc.get('Tv') is None or proc.get('Tv') <= 0: return False
        if proc.get('phi') is None: return False
        if not proc.get('workers') or len(proc['workers']) == 0: return False
        return True

    def _calculate_burst_window(self, t_now, proc):
        """
        Calculates the time interval [start, end] of the next (or current) burst.
        """
        phi = proc['phi']
        T = proc['Tv']
        worker_dv = proc['Dv']
        
        # Retrieve the real duration of the last burst measured by the controller
        measured_delta = proc.get('last_duration_round', 0)
        last_start = proc.get('last_round_start', 0)
        
        if measured_delta > 0:
            # Add a tiny 5% margin to absorb TCP jitter
            delta = measured_delta * 1.05 
        else:
            # Theoretical fallback if we don't have complete historical data yet
            num_workers = len(proc['workers'])
            fair_share = self.C_LINK / num_workers
            delta = worker_dv / fair_share

        # CASE A: Procedure currently active
        if t_now >= last_start and t_now <= (last_start + delta):
            return last_start, last_start + delta, worker_dv
            
        # CASE B: Procedure in SILENCE state (Projection)
        k = math.floor((t_now - phi) / T)
        
        start_k = phi + (k * T)
        end_k = start_k + delta
        
        if end_k > t_now:
            return start_k, end_k, worker_dv
        else:
            start_k1 = phi + ((k + 1) * T)
            return start_k1, start_k1 + delta, worker_dv

    def _build_collision_groups(self, predictable_procs, windows):
        """
        Creates the "Temporal Islands". Groups colliding procedures.
        Returns a list of lists. Ex: [['c1', 'c2'], ['c3']]
        """
        nodes = list(predictable_procs.keys())
        adj_list = {n: [] for n in nodes}

        # 1. Build the edges of the Collision Graph
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                n1, n2 = nodes[i], nodes[j]
                start1, end1, _ = windows[n1]
                start2, end2, _ = windows[n2]
                
                # Intersection condition between two intervals
                if max(start1, start2) < min(end1, end2):
                    adj_list[n1].append(n2)
                    adj_list[n2].append(n1)

        # 2. Find Connected Components (Clustering/BFS)
        visited = set()
        groups = []
        for node in nodes:
            if node not in visited:
                comp = []
                queue = [node]
                visited.add(node)
                
                while queue:
                    curr = queue.pop(0)
                    comp.append(curr)
                    for neighbor in adj_list[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                groups.append(comp)
                
        return groups

    def _handle_OptimizationRequired(self, event):
        procedures = event.procedures
        adjacency = event.adjacency
        ip_to_mac = event.ip_to_mac
        mac_to_location = event.mac_to_location
        collector_dpid_map = event.collector_dpid_map
        
        t_now = time.time()
        spines = sorted([d for d in adjacency.keys() if len(adjacency.get(d, {})) > 0])
        
        if not spines or len(spines) < 2: return

        # 1. SELECTION OF PREDICTABLE PROCEDURES
        predictable_procs = {}
        for ip, proc in procedures.items():
            if self._is_predictable(proc):
                predictable_procs[ip] = proc
            else:
                log.debug(f"[PREDICTIVE] {ip} is a Ghost. Delegated to standard route.")

        if not predictable_procs:
            return # If everything is using standard routing, we stop here

        # 2. CALCULATION OF TEMPORAL WINDOWS
        windows = {}
        for c_ip, proc in predictable_procs.items():
            windows[c_ip] = self._calculate_burst_window(t_now, proc)

        # 3. CREATION OF TEMPORAL ISLANDS
        collision_groups = self._build_collision_groups(predictable_procs, windows)
        
        log.info(f"\n[PREDICTIVE OPTIMIZER] T={t_now:.2f} | Found {len(collision_groups)} independent Temporal Islands.")

        flow_mapping = {}

        # 4. ISOLATED MIN-MAX RESOLUTION FOR EACH CLUSTER
        for group_index, group_ips in enumerate(collision_groups):
            log.info(f"  -> Resolving Cluster {group_index + 1}: Procedures {group_ips}")
            
            # LOCAL CLUSTER MEMORY: Tracks routes only for this island
            cluster_allocations = {} 
            
            # Sort procedures in the cluster by "Weight" (Total Volume) from largest to smallest
            # to place "Elephants" first and then fill the gaps with "Mice"
            procs_in_cluster = []
            for c_ip in group_ips:
                proc = predictable_procs[c_ip]
                weight = len(proc['workers']) * proc['Dv']
                procs_in_cluster.append({'c_ip': c_ip, 'proc': proc, 'weight': weight})
                
            procs_in_cluster.sort(key=lambda x: x['weight'], reverse=True)

            # Min-Max Algorithm inside the Temporal Island
            for entry in procs_in_cluster:
                target_c_ip = entry['c_ip']
                target_proc = entry['proc']
                workers = sorted(list(target_proc['workers']))
                target_start, target_end, target_worker_dv = windows[target_c_ip]
                
                c_leaf_dpid = collector_dpid_map.get(target_c_ip)
                if not c_leaf_dpid: continue

                # Iterate for each individual worker
                for w_ip in workers:
                    w_mac = ip_to_mac.get(w_ip)
                    if not w_mac: continue
                    
                    w_leaf_dpid, _ = mac_to_location.get(w_mac, (None, None))
                    if not w_leaf_dpid or w_leaf_dpid == c_leaf_dpid: continue

                    best_spine = None
                    min_drain_time = float('inf')

                    for spine in spines:
                        vol_up_pred = 0.0
                        vol_down_pred = 0.0
                        
                        # SCAN ALREADY PLACED ROUTES IN THIS CLUSTER
                        for (past_w_ip, past_c_ip), past_spine in cluster_allocations.items():
                            if past_spine != spine: continue # We don't care if it's on the other spine
                            
                            # Calculate the impact on the bottleneck
                            past_w_mac = ip_to_mac.get(past_w_ip)
                            past_w_leaf_dpid, _ = mac_to_location.get(past_w_mac, (None, None))
                            past_c_leaf_dpid = collector_dpid_map.get(past_c_ip)
                            
                            past_start, past_end, past_worker_dv = windows[past_c_ip]
                            
                            # Condition 1: Is it OUR OWN procedure? (Intra-Procedure Load)
                            if past_c_ip == target_c_ip:
                                if past_w_leaf_dpid == w_leaf_dpid: vol_up_pred += past_worker_dv
                                if past_c_leaf_dpid == c_leaf_dpid: vol_down_pred += past_worker_dv
                                
                            # Condition 2: Is it ANOTHER overlapping procedure in the cluster? (Inter-Procedure Load)
                            elif max(target_start, past_start) < min(target_end, past_end):
                                if past_w_leaf_dpid == w_leaf_dpid: vol_up_pred += past_worker_dv
                                if past_c_leaf_dpid == c_leaf_dpid: vol_down_pred += past_worker_dv

                        # Min-Max Bottleneck Calculation
                        time_up = (vol_up_pred + target_worker_dv) / self.C_LINK
                        time_down = (vol_down_pred + target_worker_dv) / self.C_LINK
                        path_bottleneck_time = max(time_up, time_down)
                        
                        if path_bottleneck_time < min_drain_time:
                            min_drain_time = path_bottleneck_time
                            best_spine = spine
                            
                    if best_spine:
                        # Save routes locally (for the loop) and globally (for deployment)
                        cluster_allocations[(w_ip, target_c_ip)] = best_spine
                        flow_mapping[(w_ip, target_c_ip)] = best_spine
                        self.global_allocations[(w_ip, target_c_ip)] = best_spine

        # 5. DEPLOY RULES
        self._deploy_flow_rules(flow_mapping, adjacency, ip_to_mac, mac_to_location, collector_dpid_map)
        if flow_mapping:
            log.info(f"[PREDICTIVE] Installed {len(flow_mapping)} optimized flows.")

    def _deploy_flow_rules(self, flow_mapping, adjacency, ip_to_mac, mac_to_location, collector_dpid_map):
        for (w_ip, c_ip), spine_dpid in flow_mapping.items():
            w_mac = ip_to_mac.get(w_ip)
            leaf_dpid, _ = mac_to_location.get(w_mac, (None, None))
            collector_dpid = collector_dpid_map.get(c_ip)
            
            if not leaf_dpid or not collector_dpid or leaf_dpid == collector_dpid: continue

            out_port_leaf = adjacency.get(leaf_dpid, {}).get(spine_dpid)
            if out_port_leaf:
                self._install_high_priority_rule(leaf_dpid, w_ip, c_ip, out_port_leaf)
                
            out_port_spine = adjacency.get(spine_dpid, {}).get(collector_dpid)
            if out_port_spine:
                self._install_high_priority_rule(spine_dpid, w_ip, c_ip, out_port_spine)

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
    core.registerNew(PredictiveIncastOptimizer)