from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.tcp import tcp
import logging
import topology_discovery

log = core.getLogger()

class IncastController(object):
    """
    SDN Controller class to manage Worker Discovery and Traffic Control 
    for distributed machine learning training procedures.
    """
    def __init__(self):
        # Dictionary to store discovered trainings
        self.trainings = {}
        
        # Defined collector IPs as per project modification
        self.collectors = ["10.0.1.1", "10.0.1.2", "10.0.1.3", "10.0.1.4"]

        # Register this object as a listener for OpenFlow events
        core.openflow.addListeners(self)
        
        # Register as a listener for the custom TopologyStable event
        core.TopologyDiscovery.addListenerByName("TopologyStable", self._handle_TopologyStable)
        
        log.info("IncastController initialized and waiting for topology stability...")

    def _handle_TopologyStable(self, event):
        """
        Triggered when the TopologyDiscovery module determines the network graph is stable.
        """
        log.info("NETWORK READY: Topology is stable with %s switches and %s links.", 
                 len(event.switches), len(event.adjacency))
        log.info("Traffic generation can now safely begin.")

    def _print_trainings_state(self):
        """
        Prints the current state of discovered trainings in an ordered format.
        """
        log.info("=== Current Discovered Trainings ===")
        for collector in sorted(self.trainings.keys()):
            workers = sorted(list(self.trainings[collector]))
            log.info("%s -> %s", collector, workers)
        log.info("====================================")

    def _handle_PacketIn(self, event):
        """
        Handles incoming packets to discover workers participating in training procedures.
        """
        packet = event.parsed
        if not packet.parsed:
            return

        if packet.find('arp'):
            self._flood_packet(event)
            return

        ip_packet = packet.find('ipv4')
        if not ip_packet:
            return

        tcp_packet = packet.find('tcp')
        
        # Worker Discovery Logic 
        # We look for TCP flows directed to known collector IPs
        if tcp_packet:
            dst_ip = str(ip_packet.dstip)
            dst_port = tcp_packet.dstport
            src_ip = str(ip_packet.srcip)

            if dst_ip in self.collectors:
                # Identification of the training procedure via (Collector IP)
                training_key = (dst_ip)
                
                if training_key not in self.trainings:
                    self.trainings[training_key] = set()
                
                if src_ip not in self.trainings[training_key]:
                    self.trainings[training_key].add(src_ip)
                    log.info("WORKER DISCOVERED: Node %s joined training @ %s", src_ip, dst_ip)
                    self._print_trainings_state()
                    

        # For the discovery phase, we allow basic connectivity (flooding)
        # This ensures TCP handshakes can complete.
        self._flood_packet(event)

    def _flood_packet(self, event):
        """
        Sends a packet out of all ports except the one it came in on.
        """
        msg = of.ofp_packet_out()
        msg.data = event.ofp
        msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
        event.connection.send(msg)


def launch():
    # Disable logs from standard discovery to keep output clean
    logging.getLogger("openflow.discovery").setLevel(logging.CRITICAL)
    
    # Launch the topology discovery module 
    topology_discovery.launch()
    
    def start_incast_logic():
        # Register the IncastController in the POX core
        core.registerNew(IncastController)
    
    # Wait for required components to be ready before starting
    core.call_when_ready(start_incast_logic, ['TopologyDiscovery', 'openflow'])