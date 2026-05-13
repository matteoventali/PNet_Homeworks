from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.tcp import tcp
from pox.lib.packet.arp import arp
import logging
import time
import topology_discovery

log = core.getLogger()

class IncastController(object):
    """
    Modular SDN Controller class to manage Worker Discovery, default routing, 
    and Traffic Characterization for distributed machine learning training procedures.
    """
    def __init__(self):
        # --- DEFINITIONS ---
        self.collectors = ["10.0.1.1", "10.0.1.2", "10.0.1.3", "10.0.1.4"]
        
        # --- UNIFIED PROCEDURE STATE ---
        # { collector_ip: {'workers': set(), 'phi': float, 'last_round': float, 'Tv': float, 'Dv': float, 'workers_bytes': dict, 'round_number': int} }
        self.procedures = {} 

        # --- TOPOLOGY & ROUTING STATE ---
        self.adjacency = {}       # { dpid: { neighbor_dpid: port_to_reach_neighbor } }
        self.edge_ports = {}      # { dpid: set(ports_facing_hosts) }
        self.mac_to_location = {} # { mac_address: (dpid, port) }
        self.mac_to_ip = {}       # { mac_address: ip_address } for parsing FlowRemoved

        # --- REGISTRATION ---
        core.openflow.addListeners(self)
        core.TopologyDiscovery.addListenerByName("TopologyStable", self._handle_TopologyStable)
        
        log.info("IncastController initialized. Waiting for topology stability...")

    # ==========================================
    # EVENT HANDLERS
    # ==========================================

    def _handle_TopologyStable(self, event):
        log.info("NETWORK READY: Topology is stable with %s switches.", len(event.switches))
        self._build_topology_graph(event)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return
        if not (packet.find('ipv4') or packet.find('arp')):
            return

        # 1. Update Network State
        self._learn_mac(packet.src, event.dpid, event.port)
        
        ip_packet = packet.find('ipv4')
        if ip_packet:
            self.mac_to_ip[packet.src] = str(ip_packet.srcip)
            self.mac_to_ip[packet.dst] = str(ip_packet.dstip)

        # 2. Protocol-Specific Processing
        if packet.find('arp'):
            self._process_arp(packet, event)
            return
            
        if packet.find('ipv4') and packet.find('tcp'):
            self._process_worker_discovery(packet)

        # 3. Forwarding Decision
        self._forward_packet(packet, event)
        
    def _handle_FlowRemoved(self, event):
        """Extracts statistics when a flow expires to estimate D_v."""
        match = event.ofp.match
        src_mac = match.dl_src
        dst_mac = match.dl_dst
        
        if not src_mac or not dst_mac:
            return
            
        src_ip = self.mac_to_ip.get(src_mac)
        dst_ip = self.mac_to_ip.get(dst_mac)
        
        # Check if this flow belongs to a known training procedure
        if dst_ip in self.procedures and src_ip in self.procedures[dst_ip]['workers']:
            byte_count = event.ofp.byte_count
            
            # Convert bytes to Megabits
            mbits = (byte_count * 8) / 1000000.0
            
            # Exclude tiny TCP control flows (e.g., pure ACKs) that don't represent the burst
            if mbits < 1.0:
                return

            proc = self.procedures[dst_ip]
            proc['workers_bytes'][src_ip] = mbits
            
            # Estimate D_v as the average data sent by workers in this round
            total_mbits = sum(proc['workers_bytes'].values())
            num_workers = len(proc['workers_bytes'])
            proc['Dv'] = total_mbits / num_workers
            
            log.debug("[TRAFFIC CHAR] %s: Worker %s finished burst.", dst_ip, src_ip)
            
            # If we collected stats for all currently known workers, print the summary
            if len(proc['workers_bytes']) == len(proc['workers']):
                log.info("[TRAFFIC CHAR] %s: Round %d completely collected!", dst_ip, proc['round_number'])
                self._print_procedures_state()

    # ==========================================
    # TOPOLOGY & MAC LEARNING MODULES
    # ==========================================

    def _build_topology_graph(self, event):
        for s in event.switches:
            self.adjacency[s] = {}
            self.edge_ports[s] = set()
            connection = core.openflow.getConnection(s)
            if connection:
                for port in connection.ports.values():
                    if port.port_no < of.OFPP_MAX:
                        self.edge_ports[s].add(port.port_no)
        
        for dpid1, port1, dpid2, port2 in event.adjacency:
            self.adjacency[dpid1][dpid2] = port1
            self.adjacency[dpid2][dpid1] = port2
            if port1 in self.edge_ports.get(dpid1, set()):
                self.edge_ports[dpid1].remove(port1)
            if port2 in self.edge_ports.get(dpid2, set()):
                self.edge_ports[dpid2].remove(port2)

    def _learn_mac(self, mac_addr, dpid, port):
        if mac_addr not in self.mac_to_location:
            self.mac_to_location[mac_addr] = (dpid, port)

    # ==========================================
    # DISCOVERY & CHARACTERIZATION MODULES
    # ==========================================

    def _process_worker_discovery(self, packet):
        ip_packet = packet.find('ipv4')
        dst_ip = str(ip_packet.dstip)
        src_ip = str(ip_packet.srcip)

        if dst_ip in self.collectors:
            current_time = time.time()
            
            # --- INITIALIZATION ---
            if dst_ip not in self.procedures:
                self.procedures[dst_ip] = {
                    'workers': set(),
                    'phi': current_time, 
                    'last_round': current_time, 
                    'Tv': 0.0, 
                    'Dv': 0.0,
                    'workers_bytes': {},
                    'round_number': 1  # Track the current round
                }
                log.info("[TRAFFIC CHAR] %s: First transmission detected (Round 1). phi_v initialized.", dst_ip)
            
            # --- T_v ESTIMATION & ROUND TRACKING ---
            else:
                time_diff = current_time - self.procedures[dst_ip]['last_round']
                # Using 25 seconds threshold to avoid false positives from heavy congestion
                if time_diff > 25:
                    self.procedures[dst_ip]['Tv'] = time_diff
                    self.procedures[dst_ip]['last_round'] = current_time
                    self.procedures[dst_ip]['workers_bytes'] = {} 
                    self.procedures[dst_ip]['round_number'] += 1 # Increment round counter
                    
                    log.info("[TRAFFIC CHAR] %s: Started Round %d! Estimated T_v = %.2f sec", 
                             dst_ip, self.procedures[dst_ip]['round_number'], time_diff)

            # --- WORKER DISCOVERY (K_v) ---
            if src_ip not in self.procedures[dst_ip]['workers']:
                self.procedures[dst_ip]['workers'].add(src_ip)
                self._print_procedures_state()

    def _print_procedures_state(self):
        """Prints a comprehensive overview of all discovered parameters."""
        log.info("=====================================================")
        log.info("            CURRENT PROCEDURES STATE                 ")
        log.info("=====================================================")
        for collector in sorted(self.procedures.keys()):
            proc = self.procedures[collector]
            workers = sorted(list(proc['workers']))
            K_v = len(workers)
            
            log.info("Collector IP : %s", collector)
            log.info("  - Current Round : %d", proc['round_number'])
            log.info("  - K_v (Workers) : %d -> %s", K_v, workers)
            log.info("  - D_v (Data)    : %.2f Mbit", proc['Dv'])
            log.info("  - T_v (Period)  : %.2f sec", proc['Tv'])
            log.info("  - phi_v (Phase) : %.2f (absolute timestamp)", proc['phi'])
            log.info("-----------------------------------------------------")

    # ==========================================
    # ROUTING & FORWARDING MODULES
    # ==========================================

    def _process_arp(self, packet, event):
        if packet.dst.isMulticast():
            self._smart_flood(packet, event.dpid, event.port)
        else:
            self._forward_packet(packet, event)

    def _forward_packet(self, packet, event):
        if packet.dst in self.mac_to_location:
            self._calculate_and_install_route(packet, event)
        else:
            self._smart_flood(packet, event.dpid, event.port)

    def _get_shortest_path(self, src_dpid, dst_dpid):
        queue = [[src_dpid]]
        visited = set([src_dpid])
        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == dst_dpid:
                return path
            for neighbor in self.adjacency.get(node, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = list(path)
                    new_path.append(neighbor)
                    queue.append(new_path)
        return None

    # ==========================================
    # OPENFLOW ACTION MODULES
    # ==========================================

    def _calculate_and_install_route(self, packet, event):
        dest_dpid, dest_port = self.mac_to_location[packet.dst]
        curr_dpid = event.dpid

        if curr_dpid == dest_dpid:
            out_port = dest_port
        else:
            path = self._get_shortest_path(curr_dpid, dest_dpid)
            if not path:
                log.warning("No path found between %s and %s!", curr_dpid, dest_dpid)
                return
            out_port = self.adjacency[curr_dpid][path[1]]

        self._install_flow_mod(packet, event, out_port)

    def _install_flow_mod(self, packet, event, out_port):
        msg = of.ofp_flow_mod()
        msg.priority = 10
        msg.idle_timeout = 5
        msg.flags = of.OFPFF_SEND_FLOW_REM 
        msg.match.dl_src = packet.src
        msg.match.dl_dst = packet.dst
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.buffer_id = event.ofp.buffer_id
        event.connection.send(msg)

    def _smart_flood(self, packet, in_dpid, in_port):
        for dpid, ports in self.edge_ports.items():
            connection = core.openflow.getConnection(dpid)
            if connection is None:
                continue
            actions = []
            for port in ports:
                if dpid == in_dpid and port == in_port:
                    continue
                actions.append(of.ofp_action_output(port=port))
            if actions:
                msg = of.ofp_packet_out()
                msg.data = packet.pack()
                msg.actions = actions
                connection.send(msg)

def launch():
    logging.getLogger("openflow.discovery").setLevel(logging.CRITICAL)
    logging.getLogger("packet").setLevel(logging.CRITICAL)
    topology_discovery.launch()
    
    def start_incast_logic():
        core.registerNew(IncastController)
    
    core.call_when_ready(start_incast_logic, ['TopologyDiscovery', 'openflow'])