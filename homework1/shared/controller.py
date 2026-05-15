from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.tcp import tcp
from pox.lib.packet.arp import arp
from pox.lib.revent import Event, EventMixin
from pox.lib.recoco import Timer
import time
import topology_discovery
import logging
import json

log = core.getLogger()

class OptimizationRequired(Event):
    def __init__(self, procedures):
        super(OptimizationRequired, self).__init__()
        self.procedures = procedures

class IncastController(EventMixin):
    _eventMixin_events = set([OptimizationRequired])

    def __init__(self, 
                 collectors=[],
                 polling_interval=1.0, 
                 silence_threshold=8.0, 
                 min_traffic_delta=15000, 
                 alpha_ewma=1,
                 log_discovery=True,
                 log_polling=True,
                 log_state=True
                ):
        
        self.collectors = collectors
        self.POLLING_INTERVAL = float(polling_interval)
        self.SILENCE_THRESHOLD = float(silence_threshold)
        self.MIN_TRAFFIC_DELTA = int(min_traffic_delta)
        self.ALPHA_EWMA = float(alpha_ewma)
        self.log_discovery = log_discovery
        self.log_polling = log_polling
        self.log_state = log_state

        self.collector_dpid_map = {} 
        self.procedures = {} 
        self.adjacency = {}       
        self.edge_ports = {}      
        self.mac_to_location = {} 
        self.mac_to_ip = {}
        self.shortest_paths = {}  # Pre-computed switch-to-switch paths
        self.start_time = time.time()  
        
        core.openflow.addListeners(self)
        core.TopologyDiscovery.addListenerByName("TopologyStable", self._handle_TopologyStable)
        Timer(self.POLLING_INTERVAL, self._request_stats, recurring=True)
        log.info("IncastController (Telemetry + ECMP) started.")

    def _request_stats(self):
        target_dpids = set(self.collector_dpid_map.values())
        for dpid in target_dpids:
            connection = core.openflow.getConnection(dpid)
            if connection:
                connection.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))

    def _handle_FlowStatsReceived(self, event):
        dpid = event.connection.dpid # Switch from which we have received the event
        current_time = time.time() - self.start_time
        procedure_deltas = {dst_ip: 0 for dst_ip in self.procedures} # Dictionary to measure the deltas of traffic for each collector

        for stat in event.stats:
            match = stat.match

            # Extracting ip addresses involved in the match
            if not match.dl_src or not match.dl_dst: continue
            src_ip = self.mac_to_ip.get(match.dl_src)
            dst_ip = self.mac_to_ip.get(match.dl_dst)
            if not src_ip or not dst_ip: continue

            # If we're considering a match of traffic for the collector
            # and we have received the stats from the egress point (switch directly connected to the collector)
            if dst_ip in self.procedures and self.collector_dpid_map.get(dst_ip) == dpid:
                proc = self.procedures[dst_ip]
                last_bytes = proc['flow_byte_trackers'].get(src_ip, 0)
                delta = stat.byte_count - last_bytes if stat.byte_count >= last_bytes else stat.byte_count
                proc['flow_byte_trackers'][src_ip] = stat.byte_count
                procedure_deltas[dst_ip] += delta

        for dst_ip, delta in procedure_deltas.items():
            if self.collector_dpid_map.get(dst_ip) != dpid: continue
            proc = self.procedures[dst_ip]

            if delta > self.MIN_TRAFFIC_DELTA:
                proc['last_traffic_time'] = current_time
                proc['accumulated_bytes'] += delta
                
                if proc['state'] == 'INIT':
                    proc['state'] = 'BURST'
                    proc['last_round_start'] = current_time
                    proc['phi'] = current_time # Initial phase detection
                    if self.log_polling: 
                        log.info("[%s] Round 1 started (Phase phi_v: %.2f)", dst_ip, proc['phi'])
                
                elif proc['state'] == 'SILENCE':
                    proc['state'] = 'BURST'
                    
                    # Tv = Time between the start of consecutive rounds
                    measured_period = current_time - proc['last_round_start']
                    proc['Tv'] = measured_period if proc['Tv'] == 0 else (1 - self.ALPHA_EWMA) * proc['Tv'] + self.ALPHA_EWMA * measured_period
                    proc['last_round_start'] = current_time
                    proc['round_number'] += 1
                    
                    if self.log_polling: 
                        log.info("[%s] Round %d started (Tv: %.2f)", dst_ip, proc['round_number'], proc['Tv'])
                    
                    if proc['round_number'] >= 2:
                        self.raiseEvent(OptimizationRequired(self.procedures))
            else:
                if proc['state'] == 'BURST' and (current_time - proc['last_traffic_time']) > self.SILENCE_THRESHOLD:
                    proc['state'] = 'SILENCE'
                    # Dv = Total burst bytes / number of active workers
                    active_workers = len(proc['workers'])
                    m_dv = (proc['accumulated_bytes'] * 8 / 1e6) / active_workers if active_workers else 0
                    proc['Dv'] = m_dv if proc['Dv'] == 0 else (1 - self.ALPHA_EWMA) * proc['Dv'] + self.ALPHA_EWMA * m_dv
                    proc['accumulated_bytes'] = 0 
                    
                    if self.log_state: 
                        self._print_procedures_state()

    def _handle_TopologyStable(self, event):
        for s in event.switches:
            self.adjacency[s], self.edge_ports[s] = {}, set()
            conn = core.openflow.getConnection(s)
            if conn:
                for p in conn.ports.values():
                    if p.port_no < of.OFPP_MAX: self.edge_ports[s].add(p.port_no)
        for d1, p1, d2, p2 in event.adjacency:
            self.adjacency[d1][d2], self.adjacency[d2][d1] = p1, p2
            if p1 in self.edge_ports[d1]: self.edge_ports[d1].remove(p1)
            if p2 in self.edge_ports[d2]: self.edge_ports[d2].remove(p2)
            
        self._precompute_paths()
        log.info("[ROUTING] All-pairs shortest paths pre-computed for ECMP.")

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
        # Checking if the packet is valid
        packet = event.parsed
        if not packet.parsed or not (packet.find('ipv4') or packet.find('arp')): return
        
        # Learning the mac address location from the packet received
        self._learn_mac(packet.src, event.dpid, event.port)
        
        # Dispatching the packet
        ip_p = packet.find('ipv4')
        arp_p = packet.find('arp')

        # Mapping the collector position
        if ip_p: # When it is an ip packet we extract ip.src and ip.dst
            src_i, dst_i = str(ip_p.srcip), str(ip_p.dstip)
            self.mac_to_ip[packet.src], self.mac_to_ip[packet.dst] = src_i, dst_i
            
            if src_i in self.collectors and src_i not in self.collector_dpid_map:
                if event.port in self.edge_ports.get(event.dpid, set()):
                    self.collector_dpid_map[src_i] = event.dpid
        elif arp_p: # When it is an arp packet we extract protosrc and protodst of the arp reply
            src_i, dst_i = str(arp_p.protosrc), str(arp_p.protodst)
            self.mac_to_ip[packet.src], self.mac_to_ip[packet.dst] = src_i, dst_i
            
            if src_i in self.collectors and src_i not in self.collector_dpid_map:
                if event.port in self.edge_ports.get(event.dpid, set()):
                    self.collector_dpid_map[src_i] = event.dpid

        # Processing the packet
        if packet.find('arp'):
            self._process_arp(packet, event)
        elif packet.find('ipv4') and packet.find('tcp'): 
            self._process_worker_discovery(packet)
            self._forward_packet(packet, event)

    def _process_arp(self, packet, event):
        if packet.dst.isMulticast(): # ARP request (destination address is broadcast)
            self._smart_flood(packet, event)
        else:   # ARP response
            self._forward_packet(packet, event)

    def _process_worker_discovery(self, packet):
        # Extracting info from the packet
        ip_p = packet.find('ipv4')
        dst_i, src_i = str(ip_p.dstip), str(ip_p.srcip)
        
        # We have to analyze only traffic for a collector
        if dst_i not in self.collectors: return
        
        # Estimating the phi value
        cur = time.time() - self.start_time
        
        if dst_i not in self.procedures:
            self.procedures[dst_i] = {'workers': {src_i}, 
                                      'phi': cur, 
                                      'last_round_start': 0, 
                                      'Tv': 0.0, 'Dv': 0.0, 
                                      'round_number': 1, 
                                      'state': 'INIT', 
                                      'last_traffic_time': cur, 
                                      'accumulated_bytes': 0, 
                                      'flow_byte_trackers': {}}
            if self.log_discovery: log.info("[DISCOVERY] %s: New procedure.", dst_i)
        else:
            self.procedures[dst_i]['workers'].add(src_i)
            
    def _learn_mac(self, mac, dpid, port):
        if mac not in self.mac_to_location: 
            self.mac_to_location[mac] = (dpid, port)

    def _forward_packet(self, packet, event):
        ip_p = packet.find('ipv4')
        
        # 1. Controlliamo se conosciamo la destinazione
        if packet.dst in self.mac_to_location:
            dst_dpid, dst_port = self.mac_to_location[packet.dst]
            
            # Scenario A: Il destinatario è già su questo switch
            if event.dpid == dst_dpid: 
                self._install_flow(packet, event.connection, dst_port)
                self._send_packet_out(event, dst_port)
                return

            # Scenario B: Dobbiamo attraversare la rete
            paths = self.shortest_paths.get(event.dpid, {}).get(dst_dpid, [])
            if paths:
                # Scegliamo il percorso con ECMP (IP o MAC come fallback per ARP)
                if ip_p:
                    path_idx = hash(str(ip_p.srcip) + str(ip_p.dstip)) % len(paths)
                else:
                    path_idx = hash(str(packet.src) + str(packet.dst)) % len(paths)
                    
                chosen_path = paths[path_idx]
                
                # --- INSTALLAZIONE END-TO-END ---
                # Installiamo le regole su tutti gli switch intermedi (es. Leaf -> Spine)
                for i in range(len(chosen_path) - 1):
                    current_dpid = chosen_path[i]
                    next_dpid = chosen_path[i+1]
                    out_port = self.adjacency[current_dpid][next_dpid]
                    
                    conn = core.openflow.getConnection(current_dpid)
                    if conn:
                        self._install_flow(packet, conn, out_port)
                        
                # Installiamo la regola sull'ULTIMO switch
                dest_conn = core.openflow.getConnection(dst_dpid)
                if dest_conn:
                    self._install_flow(packet, dest_conn, dst_port)
                    
                # Infine, "spingiamo" fuori fisicamente il pacchetto che era rimasto bloccato nel primo switch
                first_out_port = self.adjacency[event.dpid][chosen_path[1]]
                self._send_packet_out(event, first_out_port)
            else:
                log.warning("Nessun percorso trovato da %s a %s", event.dpid, dst_dpid)
        else: 
            # Non sappiamo dove sia la destinazione, facciamo flooding
            self._smart_flood(packet, event)

    def _install_flow(self, packet, connection, port):
        """Installa una regola OpenFlow su una specifica connessione (Switch)."""
        msg = of.ofp_flow_mod(priority=10, idle_timeout=60)
        msg.match.dl_src, msg.match.dl_dst = packet.src, packet.dst
        msg.actions.append(of.ofp_action_output(port=port))
        connection.send(msg)
        
    def _send_packet_out(self, event, port):
        """Invia un singolo pacchetto fuori dallo switch che ha generato il PacketIn."""
        msg = of.ofp_packet_out(data=event.ofp)
        msg.in_port = event.port
        msg.actions.append(of.ofp_action_output(port=port))
        event.connection.send(msg)

    def _smart_flood(self, packet, event):
        raw = event.ofp.data
        for dpid, ports in self.edge_ports.items():
            conn = core.openflow.getConnection(dpid)
            if not conn: continue
            actions = [of.ofp_action_output(port=p) for p in ports if not (dpid==event.dpid and p==event.port)]
            if actions: conn.send(of.ofp_packet_out(data=raw, actions=actions))

    def _print_procedures_state(self):
        log.info("--- PROCEDURES STATE ---")
        for c in sorted(self.procedures.keys()):
            p = self.procedures[c]
            log.info("%s | R:%d | K:%d | Dv:%.1f | Tv:%.1f | phi:%.1f", c, p['round_number'], len(p['workers']), p['Dv'], p['Tv'], p['phi'])


# Entry point
def launch(polling_interval=1.0, silence_threshold=8.0, alpha_ewma=0.8,
           log_discovery=True, log_polling=True, log_state=True):
    
    # Disabling useless log messages
    logging.getLogger("openflow.discovery").setLevel(logging.CRITICAL)
    logging.getLogger("packet").setLevel(logging.CRITICAL)
    
    # Launching the topology_discovery component
    topology_discovery.launch()

    # Reading the controller set
    with open('/shared/set_collector.json', 'r') as file:
        collector_set = json.load(file)["collectors"]
    
    core.registerNew(IncastController, 
                        collectors = collector_set,
                        polling_interval=polling_interval, 
                        silence_threshold=silence_threshold, 
                        min_traffic_delta=15000, 
                        alpha_ewma=alpha_ewma,
                        log_discovery=log_discovery,
                        log_polling=log_polling,
                        log_state=log_state
                    )