from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()

class IncastOptimizer(object):
    def __init__(self):
        self.C_MAX = 100.0  # Max capacity of the link in Mbps
        
        # Capacity Tracking: (dpid_A, dpid_B) -> Allocated Bandwidth (Mbps)
        self.link_alloc = {}  
        
        # Per-flow Tracking: (src_ip, dst_ip) -> {'path': [...], 'bw': Mbps}
        self.flow_alloc = {} 
        
        # Subscribe to custom events from the IncastController
        core.IncastController.addListenerByName("FlowAdmissionRequest", self._handle_Admission)
        core.IncastController.addListenerByName("FlowReleaseRequest", self._handle_Release)
        
        log.info("[OPTIMIZER] Event-Driven Fair Share Admission Control (RR + C/K) Started.")

    def _handle_Admission(self, event):
        ctrl = core.IncastController
        
        src_dpid = event.src_dpid
        dst_dpid = event.dst_dpid
        src_ip = event.src_ip
        dst_ip = event.dst_ip
        dst_port = event.dst_port
        
        # Extract learned parameters
        Tv = event.proc_info.get('Tv', 0.0)
        workers_count = len(event.proc_info.get('workers', []))

        # 1. Bypass local traffic (source and destination on the same leaf switch)
        if src_dpid == dst_dpid:
            self._install_rule(src_dpid, src_ip, dst_ip, dst_port)
            event.admitted = True
            event.out_port = dst_port
            return

        # 2. Bypass if flow has already been allocated
        if (src_ip, dst_ip) in self.flow_alloc:
            path = self.flow_alloc[(src_ip, dst_ip)]['path']
            event.admitted = True
            event.out_port = ctrl.adjacency[src_dpid][path[1]]
            return

        # Fetch possible shortest paths (Spines)
        paths = ctrl.shortest_paths.get(src_dpid, {}).get(dst_dpid, [])
        if not paths: 
            return # event.admitted remains False

        # ---------------------------------------------------------
        # PHASE 1: DISCOVERY (ROUND 1) - BLIND ROUND ROBIN
        # ---------------------------------------------------------
        if Tv == 0.0 or workers_count == 0:
            # We don't check capacity, we spread the load.
            # Using hash on source IP to achieve pseudo-Round Robin
            path_idx = hash(src_ip + dst_ip) % len(paths)
            best_path = paths[path_idx]
            
            # Install rules, but DO NOT deduct bandwidth from ledger (link_alloc)
            for i in range(len(best_path) - 1):
                dpid = best_path[i]
                out_port = ctrl.adjacency[dpid][best_path[i+1]]
                self._install_rule(dpid, src_ip, dst_ip, out_port)
            self._install_rule(dst_dpid, src_ip, dst_ip, dst_port)
            
            log.debug("[DISCOVERY] %s assigned to Spine S%s (Round Robin)", src_ip, best_path[1])
            event.admitted = True
            event.out_port = ctrl.adjacency[src_dpid][best_path[1]]
            return

        # ---------------------------------------------------------
        # PHASE 2: STEADY STATE (ROUND 2+) - FAIR SHARE CAPACITY
        # ---------------------------------------------------------
        # Compute Fair Share Rate (Expected Worker Rate)
        bw_req = self.C_MAX / workers_count
        log.debug("[SMART] Procedure %s has K=%d. Fair Share Rate: %.1f Mbps.", dst_ip, workers_count, bw_req)

        # Filter paths based on residual capacity using bw_req
        valid_paths = []
        for p in paths:
            valid = True
            bottleneck_res = self.C_MAX
            for i in range(len(p) - 1):
                u, v = p[i], p[i+1]
                res = self.C_MAX - self.link_alloc.get((u, v), 0.0)
                if res < bw_req:
                    valid = False
                    break # Link is saturated for this slice
                bottleneck_res = min(bottleneck_res, res)
            if valid:
                valid_paths.append((p, bottleneck_res))

        if not valid_paths:
            log.info("[BLOCKED] Network saturated for %s -> %s (Req: %.1f Mbps)", src_ip, dst_ip, bw_req)
            return # event.admitted remains False

        # Choose the least loaded path
        valid_paths.sort(key=lambda x: x[1], reverse=True)
        best_path = valid_paths[0][0]

        # PHYSICALLY RESERVE the resources
        for i in range(len(best_path) - 1):
            u, v = best_path[i], best_path[i+1]
            self.link_alloc[(u, v)] = self.link_alloc.get((u, v), 0.0) + bw_req
            
        self.flow_alloc[(src_ip, dst_ip)] = {'path': best_path, 'bw': bw_req}

        # Install rules
        for i in range(len(best_path) - 1):
            dpid = best_path[i]
            out_port = ctrl.adjacency[dpid][best_path[i+1]]
            self._install_rule(dpid, src_ip, dst_ip, out_port)
            
        self._install_rule(dst_dpid, src_ip, dst_ip, dst_port)
        
        log.info("[ADMISSION] SUCCESS - %s -> %s via S%s (%.1f Mbps)", src_ip, dst_ip, best_path[1], bw_req)
        
        # Modify the event to inform the controller
        event.admitted = True
        event.out_port = ctrl.adjacency[src_dpid][best_path[1]]

    def _handle_Release(self, event):
        """ Releases the exact resources allocated to the expired flow """
        src_ip = event.src_ip
        dst_ip = event.dst_ip
        
        if (src_ip, dst_ip) in self.flow_alloc:
            alloc = self.flow_alloc[(src_ip, dst_ip)]
            path = alloc['path']
            bw = alloc['bw']
            
            # Release bandwidth from links
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                self.link_alloc[(u, v)] = max(0.0, self.link_alloc.get((u, v), 0.0) - bw)
                
            del self.flow_alloc[(src_ip, dst_ip)]
            log.info("[RELEASE] Freed %.1f Mbps for %s -> %s", bw, src_ip, dst_ip)

    def _install_rule(self, dpid, src_ip, dst_ip, port):
        msg = of.ofp_flow_mod()
        msg.priority = 100
        msg.idle_timeout = 2  # CRITICAL: Expires if inactive for 2 sec
        msg.flags |= of.OFPFF_SEND_FLOW_REM  # Requests a FlowRemoved notification
        
        msg.match.dl_type = 0x0800
        msg.match.nw_src = src_ip
        msg.match.nw_dst = dst_ip
        msg.actions.append(of.ofp_action_output(port=port))
        
        conn = core.openflow.getConnection(dpid)
        if conn: conn.send(msg)

def launch():
    core.registerNew(IncastOptimizer)