from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.tcp import tcp
from pox.lib.packet.arp import arp
import logging
import topology_discovery

log = core.getLogger()

class IncastController(object):
    """
    Modular SDN Controller class to manage Worker Discovery and default routing 
    for distributed machine learning training procedures.
    """
    def __init__(self):
        # --- DISCOVERY STATE ---
        self.trainings = {} # { collector_ip: set(worker_ips) }
        self.collectors = ["10.0.1.1", "10.0.1.2", "10.0.1.3", "10.0.1.4"]
        
        # --- TOPOLOGY & ROUTING STATE ---
        self.adjacency = {}      # { dpid: { neighbor_dpid: port_to_reach_neighbor } }
        self.edge_ports = {}     # { dpid: set(ports_facing_hosts) }
        self.mac_to_location = {} # { mac_address: (dpid, port) }

        # --- REGISTRATION ---
        core.openflow.addListeners(self)
        core.TopologyDiscovery.addListenerByName("TopologyStable", self._handle_TopologyStable)
        
        log.info("IncastController initialized. Waiting for topology stability...")

    # ==========================================
    # EVENT HANDLERS
    # ==========================================

    def _handle_TopologyStable(self, event):
        """Builds the network graph when topology stabilizes."""
        log.info("NETWORK READY: Topology is stable with %s switches.", len(event.switches))
        self._build_topology_graph(event)

    def _handle_PacketIn(self, event):
        """
        Main Packet Dispatcher. Delegates processing to specific modules.
        """
        packet = event.parsed
        if not packet.parsed:
            return
        if not (packet.find('ipv4') or packet.find('arp')):
            return

        # 1. Update Network State
        self._learn_mac(packet.src, event.dpid, event.port)

        # 2. Protocol-Specific Processing
        if packet.find('arp'):
            self._process_arp(packet, event)
            return
            
        if packet.find('ipv4') and packet.find('tcp'):
            self._process_worker_discovery(packet)

        # 3. Forwarding Decision
        self._forward_packet(packet, event)

    # ==========================================
    # TOPOLOGY & MAC LEARNING MODULES
    # ==========================================

    def _build_topology_graph(self, event):
        """Extracts adjacency and edge ports from the topology event."""
        # Initialize
        for s in event.switches:
            self.adjacency[s] = {}
            self.edge_ports[s] = set()
            connection = core.openflow.getConnection(s)
            if connection:
                for port in connection.ports.values():
                    if port.port_no < of.OFPP_MAX:
                        self.edge_ports[s].add(port.port_no)
        
        # Populate adjacency and refine edge ports
        for dpid1, port1, dpid2, port2 in event.adjacency:
            self.adjacency[dpid1][dpid2] = port1
            self.adjacency[dpid2][dpid1] = port2
            
            if port1 in self.edge_ports.get(dpid1, set()):
                self.edge_ports[dpid1].remove(port1)
            if port2 in self.edge_ports.get(dpid2, set()):
                self.edge_ports[dpid2].remove(port2)
                
        log.info("Topology graph built. Edge Ports calculated. Ready for traffic.")

    def _learn_mac(self, mac_addr, dpid, port):
        """Registers the location of a host."""
        if mac_addr not in self.mac_to_location:
            self.mac_to_location[mac_addr] = (dpid, port)

    # ==========================================
    # DISCOVERY MODULES
    # ==========================================

    def _process_worker_discovery(self, packet):
        """Identifies new workers joining a training procedure."""
        ip_packet = packet.find('ipv4')
        dst_ip = str(ip_packet.dstip)
        src_ip = str(ip_packet.srcip)

        if dst_ip in self.collectors:
            if dst_ip not in self.trainings:
                self.trainings[dst_ip] = set()
            
            if src_ip not in self.trainings[dst_ip]:
                self.trainings[dst_ip].add(src_ip)
                self._print_trainings_state()

    def _print_trainings_state(self):
        """Utility to print the current state of procedures."""
        log.info("=== Current Discovered Trainings ===")
        for collector in sorted(self.trainings.keys()):
            workers = sorted(list(self.trainings[collector]))
            log.info("%s -> %s", collector, workers)
        log.info("====================================")

    # ==========================================
    # ROUTING & FORWARDING MODULES
    # ==========================================

    def _process_arp(self, packet, event):
        """Handles ARP requests and replies."""
        if packet.dst.isMulticast():
            self._smart_flood(packet, event.dpid, event.port)
        else:
            self._forward_packet(packet, event)

    def _forward_packet(self, packet, event):
        """Decides whether to route the packet or flood it to edge ports."""
        if packet.dst in self.mac_to_location:
            self._calculate_and_install_route(packet, event)
        else:
            self._smart_flood(packet, event.dpid, event.port)

    def _get_shortest_path(self, src_dpid, dst_dpid):
        """BFS implementation to find the shortest path."""
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
        """Finds the output port and installs the default FlowMod rule."""
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
        """Constructs and sends the OpenFlow FlowMod message."""
        msg = of.ofp_flow_mod()
        
        # Transitional routing settings 
        msg.priority = 10
        msg.idle_timeout = 5
        
        msg.match.dl_src = packet.src
        msg.match.dl_dst = packet.dst
        
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.buffer_id = event.ofp.buffer_id
        
        event.connection.send(msg)

    def _smart_flood(self, packet, in_dpid, in_port):
        """Sends PacketOut messages exclusively to edge ports."""
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