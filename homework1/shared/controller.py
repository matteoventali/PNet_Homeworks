from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.revent import Event, EventMixin
from pox.lib.recoco import Timer
import time
import topology_discovery
import logging
import json

# Standard POX Logger
log = core.getLogger()

# Setup logger files
def setup_file_logger(name, filename):
    handler = logging.FileHandler(filename, mode='w')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

class OptimizationRequired(Event):
    def __init__(self, procedures, adjacency, edge_ports, mac_to_ip, mac_to_location, collector_dpid_map):
        super(OptimizationRequired, self).__init__()
        self.procedures = procedures
        self.adjacency = adjacency
        self.edge_ports = edge_ports
        self.mac_to_location = mac_to_location
        self.collector_dpid_map = collector_dpid_map
        self.ip_to_mac = {ip: mac for mac, ip in mac_to_ip.items()}
        self.spines = sorted([d for d in adjacency.keys() if len(edge_ports.get(d, set())) == 0])

class IncastController(EventMixin):
    _eventMixin_events = set([OptimizationRequired])

    def __init__(self, collectors=[], polling_interval=1.0, silence_threshold=8.0,
                 inactivity_coefficient=2, min_traffic_delta=15000, alpha_ewma=1,
                ):
        
        self.collectors = collectors
        self.INACTIVITY_COEFFICIENT = float(inactivity_coefficient)
        self.POLLING_INTERVAL = float(polling_interval)
        self.SILENCE_THRESHOLD = float(silence_threshold)
        self.MIN_TRAFFIC_DELTA = int(min_traffic_delta)
        self.ALPHA_EWMA = float(alpha_ewma)
        
        # Dedicated File Loggers
        self.discovery_log = setup_file_logger("discovery", "/shared/discovery.log")
        self.telemetry_log = setup_file_logger("telemetry", "/shared/persistent_telemetry.log")
        self.state_log = setup_file_logger("state", "/shared/state.log")

        self.collector_dpid_map = {} # Map collector -> dpid
        self.procedures = {} # Procedures dictionary
        self.adjacency = {}  # Graph representation
        self.edge_ports = {} # (dpid, port) that are edges in the topology (not connected to other switches)
        self.mac_to_location = {} # Map mac -> (dpid, port)
        self.mac_to_ip = {} # Map mac -> ip address
        self.shortest_paths = {} # Shortest paths
        self.start_time = time.time() # Global start time clock for the controller
        
        # Telemetry for Link Utilization
        self.port_stats = {}
        self.link_load = {}
        
        core.openflow.addListeners(self)
        core.TopologyDiscovery.addListenerByName("TopologyStable", self._handle_TopologyStable)
        Timer(self.POLLING_INTERVAL, self._request_stats, recurring=True)
        
        log.info("Monitoring component started")

    def _request_stats(self):
        self._print_global_link_utilization()
        
        # Requesting stats to leaf switches connected to collectors
        target_dpids = set(self.collector_dpid_map.values())
        for dpid in target_dpids:
            connection = core.openflow.getConnection(dpid)
            if connection:
                connection.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))
        
        # Requesting port stats to all the switches
        for connection in core.openflow.connections:
            connection.send(of.ofp_stats_request(body=of.ofp_port_stats_request()))

    def _handle_PortStatsReceived(self, event):
        dpid = event.connection.dpid
        
        # Analyzing each stats received
        for stat in event.stats:
            if stat.port_no >= of.OFPP_MAX: continue
            
            key = (dpid, stat.port_no)
            current_tx = stat.tx_bytes
            current_rx = stat.rx_bytes
            
            if key in self.port_stats:
                old_tx, old_rx = self.port_stats[key]
                
                delta_tx = current_tx - old_tx
                delta_rx = current_rx - old_rx
                
                tx_mbps = (delta_tx * 8) / (self.POLLING_INTERVAL * 1e6)
                rx_mbps = (delta_rx * 8) / (self.POLLING_INTERVAL * 1e6)
                
                # Salviamo una tupla: (Upstream, Downstream)
                self.link_load[key] = (tx_mbps, rx_mbps)
                
            # Aggiorniamo la memoria per il prossimo ciclo salvando la tupla
            self.port_stats[key] = (current_tx, current_rx)

    def _print_global_link_utilization(self):
        lines = []
        
        for dpid in sorted(self.adjacency.keys()):
            # We evaluate the topology purely from the perspective of Leaf switches
            is_spine = len(self.edge_ports.get(dpid, set())) == 0
            if is_spine: 
                continue 

            for neighbor_dpid, port_no in self.adjacency.get(dpid, {}).items():
                # Retrieve the (tx_mbps, rx_mbps) tuple. Default to (0.0, 0.0) if the link is idle
                load = self.link_load.get((dpid, port_no), (0.0, 0.0))
                
                # Error prevention: Handle edge cases where legacy single float values 
                # might still be temporarily cached in memory
                if isinstance(load, tuple):
                    up_mbps, down_mbps = load
                else:
                    up_mbps, down_mbps = 0.0, 0.0
                
                # Format and append the link utilization row
                lines.append(f"  (S{dpid}, S{neighbor_dpid}) -> Upstream: {up_mbps:>5.1f} Mbps | Downstream: {down_mbps:>5.1f} Mbps")
        
        if lines:
            # Define the telemetry block layout and borders
            block = "="*72 + "\n"
            block += "                           GLOBAL LINK LOAD\n"
            block += "-"*72 + "\n"
            block += "\n".join(lines) + "\n"
            block += "="*72 + "\n"
            
            # Output to the main POX log and overwrite the shared file for real-time visualization
            self.telemetry_log.info(block)
            with open("/shared/telemetry.log", "w") as f:
                f.write(block)

    def _handle_ErrorIn(self, event):
        error_type = event.type
        error_code = event.code
        dpid = event.dpid
        
        log.error("[OPENFLOW ERROR] Switch S%s rejected this command! Type: %s, Code: %s", 
                  dpid, error_type, error_code)

    def _handle_FlowStatsReceived(self, event):
        dpid = event.connection.dpid
        current_time = time.time() - self.start_time
        procedure_deltas = {dst_ip: 0 for dst_ip in self.procedures}

        for stat in event.stats:
            match = stat.match
            
            # Extracting src and dst ip addresses from the match
            if not match.dl_src or not match.dl_dst: continue
            src_ip = self.mac_to_ip.get(match.dl_src)
            dst_ip = self.mac_to_ip.get(match.dl_dst)
            if not src_ip or not dst_ip: continue
            
            # Considering only stats related to switches connected to collectors
            # and considering flows only directed to collectors
            if dst_ip in self.procedures and self.collector_dpid_map.get(dst_ip) == dpid:
                proc = self.procedures[dst_ip]
                last_bytes = proc['flow_byte_trackers'].get(src_ip, 0)
                delta = stat.byte_count - last_bytes if stat.byte_count >= last_bytes else stat.byte_count
                proc['flow_byte_trackers'][src_ip] = stat.byte_count
                procedure_deltas[dst_ip] += delta

        # Analyzing deltas of traffic
        for dst_ip, delta in procedure_deltas.items():
            if self.collector_dpid_map.get(dst_ip) != dpid: continue
            
            proc = self.procedures[dst_ip]
            
            # If significative traffic amount detected
            if delta > self.MIN_TRAFFIC_DELTA:
                proc['last_traffic_time'] = current_time
                proc['accumulated_bytes'] += delta
                
                # Transition from INIT to BURST state
                if proc['state'] == 'INIT':
                    proc['state'] = 'BURST'
                    proc['last_round_start'] = current_time
                    proc['phi'] = current_time
                    self.discovery_log.info("[%s] Round 1 started (Phase phi_v: %.2f)", dst_ip, proc['phi'])
                
                # Transition from SILENCE to BURST state
                elif proc['state'] == 'SILENCE':
                    proc['state'] = 'BURST'
                    measured_period = current_time - proc['last_round_start']
                    proc['Tv'] = measured_period if proc['Tv'] == 0 else (1 - self.ALPHA_EWMA) * proc['Tv'] + self.ALPHA_EWMA * measured_period
                    proc['last_round_start'] = current_time
                    proc['round_number'] += 1
                    self.discovery_log.info("[%s] Round %d started (Tv: %.2f)", dst_ip, proc['round_number'], proc['Tv'])
                    
                    # We need to recompute the routes
                    if proc['round_number'] >= 2:
                        self.raiseEventNoErrors(OptimizationRequired, 
                                                    self.procedures, 
                                                    self.adjacency, 
                                                    self.edge_ports, 
                                                    self.mac_to_ip, 
                                                    self.mac_to_location, 
                                                    self.collector_dpid_map)
            else:
                # Transition from BURST to SILENCE
                if proc['state'] == 'BURST' and (current_time - proc['last_traffic_time']) > self.SILENCE_THRESHOLD:
                    proc['state'] = 'SILENCE'
                    active_workers = len(proc['workers'])
                    m_dv = (proc['accumulated_bytes'] * 8 / 1e6) / active_workers if active_workers else 0
                    proc['Dv'] = m_dv if proc['Dv'] == 0 else (1 - self.ALPHA_EWMA) * proc['Dv'] + self.ALPHA_EWMA * m_dv
                    proc['accumulated_bytes'] = 0 
                    proc['last_duration_round'] = current_time - proc['last_round_start'] - self.SILENCE_THRESHOLD
                    self._print_procedures_state()
                
                # Transition from SILENCE to DEAD (removed from procedures)
                elif proc['state'] == 'SILENCE' and proc['Tv'] > 0:
                    measured_period = current_time - proc['last_traffic_time']
                    if measured_period > (proc['Tv'] * self.INACTIVITY_COEFFICIENT):
                        self.discovery_log.info("[LIFECYCLE] Dead procedure %s", dst_ip)
                        del self.procedures[dst_ip]

                        # We need to recompute the routes
                        self.raiseEventNoErrors(OptimizationRequired, 
                                                    self.procedures, 
                                                    self.adjacency, 
                                                    self.edge_ports, 
                                                    self.mac_to_ip, 
                                                    self.mac_to_location, 
                                                    self.collector_dpid_map)

    def _handle_TopologyStable(self, event):
        for s in event.switches:
            self.adjacency[s], self.edge_ports[s] = {}, set()
            conn = core.openflow.getConnection(s)
            if conn:
                for p in conn.ports.values():
                    if p.port_no < of.OFPP_MAX: self.edge_ports[s].add(p.port_no)
        
        # Creating the adjacency matrix representing the graph
        for d1, p1, d2, p2 in event.adjacency:
            self.adjacency[d1][d2], self.adjacency[d2][d1] = p1, p2
            if p1 in self.edge_ports[d1]: self.edge_ports[d1].remove(p1)
            if p2 in self.edge_ports[d2]: self.edge_ports[d2].remove(p2)
        
        # Precomputing the shortest paths
        self._precompute_paths()

    def _precompute_paths(self):
        switches = list(self.adjacency.keys())
        for src in switches:
            self.shortest_paths[src] = {}
            for dst in switches:
                if src == dst: continue
                self.shortest_paths[src][dst] = self._get_paths_bfs(src, dst)

    def _get_paths_bfs(self, src, dst):
        q, paths, min_l = [[src]], [], float('inf')
        while q:
            p = q.pop(0)
            if len(p) > min_l: break
            if p[-1] == dst:
                paths.append(p); min_l = len(p)
                continue
            for n in self.adjacency.get(p[-1], {}):
                if n not in p: q.append(p + [n])
        return paths

    def _handle_PacketIn(self, event):
        # We have to manage only ipv4 and arp messages
        packet = event.parsed
        if not packet.parsed or not (packet.find('ipv4') or packet.find('arp')): return
        
        # Memorizing the mac location
        self._learn_mac(packet.src, event.dpid, event.port)
        
        # Understanding the packet
        ip_p = packet.find('ipv4')
        arp_p = packet.find('arp')

        # Learning the collector position
        if ip_p:
            src_i, dst_i = str(ip_p.srcip), str(ip_p.dstip)
            self.mac_to_ip[packet.src], self.mac_to_ip[packet.dst] = src_i, dst_i
            if src_i in self.collectors and src_i not in self.collector_dpid_map:
                if event.port in self.edge_ports.get(event.dpid, set()):
                    self.collector_dpid_map[src_i] = event.dpid
        elif arp_p:
            src_i, dst_i = str(arp_p.protosrc), str(arp_p.protodst)
            self.mac_to_ip[packet.src], self.mac_to_ip[packet.dst] = src_i, dst_i
            if src_i in self.collectors and src_i not in self.collector_dpid_map:
                if event.port in self.edge_ports.get(event.dpid, set()):
                   self.collector_dpid_map[src_i] = event.dpid
        
        # Dispatching the packet
        if arp_p:
            self._process_arp(packet, event)
        elif ip_p and packet.find('tcp'): 
            self._process_worker_discovery(packet)
            self._forward_packet(packet, event)

    def _process_arp(self, packet, event):
        if packet.dst.isMulticast(): # ARP request
            self._smart_flood(packet, event)
        else: # ARP reply
            self._forward_packet(packet, event)

    def _process_worker_discovery(self, packet):
        # Extracting info from the ip packet
        ip_p = packet.find('ipv4')
        dst_i, src_i = str(ip_p.dstip), str(ip_p.srcip)
        
        # We must consider only traffic destinated to the collectors
        if dst_i not in self.collectors: return

        # Memorizing/updating the procedure parameters
        cur = time.time() - self.start_time
        if dst_i not in self.procedures:
            self.procedures[dst_i] = {'workers': {src_i}, 
                                      'phi': cur, 
                                      'last_round_start': 0, 
                                      'Tv': 0.0, 'Dv': 0.0, 
                                      'round_number': 1,
                                      'last_duration_round': 0.0, 
                                      'state': 'INIT', 
                                      'last_traffic_time': cur, 
                                      'accumulated_bytes': 0, 
                                      'flow_byte_trackers': {}}
            self.discovery_log.info("[DISCOVERY] %s: New procedure.", dst_i)
        else:
            self.procedures[dst_i]['workers'].add(src_i)
            
    def _learn_mac(self, mac, dpid, port):
        if mac not in self.mac_to_location: 
            self.mac_to_location[mac] = (dpid, port)

    def _forward_packet(self, packet, event):
        ip_p = packet.find('ipv4')
        
        # If we know the destination location
        if packet.dst in self.mac_to_location:
            dst_dpid, dst_port = self.mac_to_location[packet.dst]
            
            if event.dpid == dst_dpid: 
                self._install_flow(packet, event.connection, dst_port)
                self._send_packet_out(event, dst_port)
                return
            
            # The packet must go trough another switch
            paths = self.shortest_paths.get(event.dpid, {}).get(dst_dpid, [])
            if paths:
                
                # Choosing a path as a temporary route
                if ip_p:
                    path_idx = hash(str(ip_p.srcip) + str(ip_p.dstip)) % len(paths)
                else:
                    path_idx = hash(str(packet.src) + str(packet.dst)) % len(paths)
                chosen_path = paths[path_idx]
                
                # Installing the path
                for i in range(len(chosen_path) - 1):
                    current_dpid = chosen_path[i]
                    next_dpid = chosen_path[i+1]
                    out_port = self.adjacency[current_dpid][next_dpid]
                    conn = core.openflow.getConnection(current_dpid)
                    if conn: self._install_flow(packet, conn, out_port)
                
                dest_conn = core.openflow.getConnection(dst_dpid)
                if dest_conn: self._install_flow(packet, dest_conn, dst_port)
                first_out_port = self.adjacency[event.dpid][chosen_path[1]]
                self._send_packet_out(event, first_out_port)
            else:
                log.warning("No path from %s to %s", event.dpid, dst_dpid)
        else: 
            self._smart_flood(packet, event)

    def _install_flow(self, packet, connection, port):
        msg = of.ofp_flow_mod(priority=10, idle_timeout=120)
        msg.match.dl_src, msg.match.dl_dst = packet.src, packet.dst
        msg.actions.append(of.ofp_action_output(port=port))
        connection.send(msg)
        
    def _send_packet_out(self, event, port):
        msg = of.ofp_packet_out(data=event.ofp)
        msg.in_port = event.port
        msg.actions.append(of.ofp_action_output(port=port))
        event.connection.send(msg)

    def _smart_flood(self, packet, event):
        # Flooding the packet only on edges ports (ports not attached to other switches)
        raw = event.ofp.data
        for dpid, ports in self.edge_ports.items():
            conn = core.openflow.getConnection(dpid)
            if not conn: continue
            actions = [of.ofp_action_output(port=p) for p in ports if not (dpid==event.dpid and p==event.port)]
            if actions: conn.send(of.ofp_packet_out(data=raw, actions=actions))

    def _print_procedures_state(self):
        self.state_log.info("--- PROCEDURES STATE ---")
        for c in sorted(self.procedures.keys()):
            p = self.procedures[c]
            self.state_log.info("%s | R:%d | K:%d | Dv:%.1f | Tv:%.1f | phi:%.1f | last round:%.1f", c, p['round_number'], len(p['workers']), p['Dv'], p['Tv'], p['phi'], p['last_duration_round'])

# Entry point
def launch(polling_interval=1.0, silence_threshold=8.0, inactivity_coefficient=2, alpha_ewma=1):
    # Hiding useless logs
    logging.getLogger("openflow.discovery").setLevel(logging.CRITICAL)
    logging.getLogger("packet").setLevel(logging.CRITICAL)
    
    # Launching topology discovery component
    topology_discovery.launch()

    # Fetching the collector_set 
    with open('/shared/set_collector.json', 'r') as file:
        collector_set = json.load(file)["collectors"]
    
    # Registering the component
    core.registerNew(IncastController, 
                     collectors=collector_set, 
                     polling_interval=polling_interval, 
                     silence_threshold=silence_threshold, 
                     inactivity_coefficient=inactivity_coefficient, 
                     alpha_ewma=alpha_ewma
                    )