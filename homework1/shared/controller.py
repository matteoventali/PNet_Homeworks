# file: pox/ext/project1.py

from pox.core import core
import pox.openflow.libopenflow_01 as of

from pox.lib.packet.ipv4 import ipv4
from pox.lib.packet.tcp import tcp

import time

log = core.getLogger()

#
# Each training procedure is identified ONLY
# by the collector IP address.
#
COLLECTORS = {
    "10.0.1.1",
    "10.0.1.2",
    "10.0.1.3",
    "10.0.1.4",
}

#
# training_state structure:
#
# {
#   collector_ip: {
#       "workers": {
#           worker_ip: {
#               "collector_port": int,
#               "first_seen": timestamp,
#               "last_seen": timestamp,
#               "flow_count": int
#           }
#       },
#       "flows": [],
#       "first_seen": timestamp,
#       "last_seen": timestamp
#   }
# }
#
training_state = {}


class ProjectController(object):

    def __init__(self, connection):
        self.connection = connection
        self.dpid = connection.dpid
        
        # L2 Learning table specific to this switch: {MAC_ADDRESS: PORT}
        # Used to route traffic without flooding the network.
        self.mac_to_port = {}

        connection.addListeners(self)

        log.info("Switch connected: %s", self.dpid)

    def register_flow(self, worker_ip, collector_ip, collector_port):
        """
        Register worker activity for a training procedure.
        """
        now = time.time()

        #
        # Create training if needed
        #
        if collector_ip not in training_state:
            training_state[collector_ip] = {
                "workers": {},
                "flows": [],
                "first_seen": now,
                "last_seen": now
            }
            log.info("[NEW TRAINING] Collector=%s", collector_ip)

        training = training_state[collector_ip]
        training["last_seen"] = now

        #
        # Register worker
        #
        if worker_ip not in training["workers"]:
            training["workers"][worker_ip] = {
                "collector_port": collector_port,
                "first_seen": now,
                "last_seen": now,
                "flow_count": 0
            }
            log.info("[NEW WORKER] %s joined training %s", worker_ip, collector_ip)

        worker = training["workers"][worker_ip]
        worker["last_seen"] = now
        worker["flow_count"] += 1

        #
        # Save flow information
        #
        training["flows"].append({
            "worker_ip": worker_ip,
            "collector_port": collector_port,
            "timestamp": now
        })

        #
        # Print current training state
        #
        log.info("====================================")
        log.info("Training collector: %s", collector_ip)
        log.info("Detected workers: %d", len(training["workers"]))

        for ip, info in training["workers"].items():
            log.info(
                "Worker=%s CollectorPort=%s Flows=%d",
                ip,
                info["collector_port"],
                info["flow_count"]
            )
        log.info("====================================")

    def _handle_PacketIn(self, event):
        packet = event.parsed

        if not packet:
            return

        # 1. L2 LEARNING: Learn the port associated with the source MAC address
        self.mac_to_port[packet.src] = event.port

        # 2. DISCOVERY LOGIC
        ip_packet = packet.find("ipv4")
        tcp_segment = packet.find("tcp")

        # Process only IPv4 TCP traffic for discovery
        if ip_packet is not None and tcp_segment is not None:
            src_ip = str(ip_packet.srcip)
            dst_ip = str(ip_packet.dstip)

            # If the traffic is going from a Worker to a known Collector
            if dst_ip in COLLECTORS:
                self.register_flow(src_ip, dst_ip, tcp_segment.dstport)

        # 3. FORWARDING LOGIC
        msg = of.ofp_packet_out()
        msg.data = event.ofp

        # If we know the destination MAC's port, forward it directly
        if packet.dst in self.mac_to_port:
            out_port = self.mac_to_port[packet.dst]
            msg.actions.append(of.ofp_action_output(port=out_port))

            # Install a temporary flow rule on the switch to offload the controller
            fm = of.ofp_flow_mod()
            fm.match.dl_dst = packet.dst
            fm.match.dl_src = packet.src
            fm.idle_timeout = 10  # Seconds before the rule expires
            fm.hard_timeout = 0
            fm.actions.append(of.ofp_action_output(port=out_port))
            self.connection.send(fm)

        else:
            # If destination is unknown (e.g., initial ARP requests), flood the packet
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))

        self.connection.send(msg)


def start_switch(event):
    log.info("Controller attached to switch %s", event.dpid)
    ProjectController(event.connection)


def launch():
    log.info("Project1 SDN controller started")
    core.openflow.addListenerByName("ConnectionUp", start_switch)