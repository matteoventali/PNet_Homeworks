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

        connection.addListeners(self)

        log.info(
            "Switch connected: %s",
            self.dpid
        )

    def install_flow_rule(
        self,
        event,
        ip_packet,
        tcp_segment
    ):
        """
        Install a temporary forwarding rule.
        """

        msg = of.ofp_flow_mod()

        msg.match.dl_type = 0x0800
        msg.match.nw_proto = 6

        msg.match.nw_src = ip_packet.srcip
        msg.match.nw_dst = ip_packet.dstip

        msg.match.tp_src = tcp_segment.srcport
        msg.match.tp_dst = tcp_segment.dstport

        msg.idle_timeout = 30
        msg.hard_timeout = 0
        msg.priority = 100

        #
        # Temporary behavior:
        # flood packets
        #
        msg.actions.append(
            of.ofp_action_output(
                port=of.OFPP_FLOOD
            )
        )

        msg.data = event.ofp

        self.connection.send(msg)

    def register_flow(
        self,
        worker_ip,
        collector_ip,
        collector_port
    ):
        """
        Register worker activity for a training.
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

            log.info(
                "[NEW TRAINING] Collector=%s",
                collector_ip
            )

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

            log.info(
                "[NEW WORKER] %s joined training %s",
                worker_ip,
                collector_ip
            )

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
        log.info(
            "Training collector: %s",
            collector_ip
        )

        log.info(
            "Detected workers: %d",
            len(training["workers"])
        )

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

        #
        # IPv4 only
        #
        ip_packet = packet.find("ipv4")

        if ip_packet is None:
            return

        #
        # TCP only
        #
        tcp_segment = packet.find("tcp")

        if tcp_segment is None:
            return

        worker_ip = str(ip_packet.srcip)
        collector_ip = str(ip_packet.dstip)

        collector_port = tcp_segment.dstport

        #
        # Ignore non-collector traffic
        #
        if collector_ip not in COLLECTORS:
            return

        #
        # Register flow
        #
        self.register_flow(
            worker_ip,
            collector_ip,
            collector_port
        )

        #
        # Install flow rule
        #
        #self.install_flow_rule(
        #    event,
        #   ip_packet,
        #    tcp_segment
        #)


def start_switch(event):

    log.info(
        "Controller attached to switch %s",
        event.dpid
    )

    ProjectController(event.connection)


def launch():

    log.info("Project1 SDN controller started")

    core.openflow.addListenerByName(
        "ConnectionUp",
        start_switch
    )