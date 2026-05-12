"""
Main Controller for POX
"""

from pox.core import core
import logging
import topology_discovery

log = core.getLogger()

def _handle_TopologyStable(event):
    log.info("Controller notified: Topology is stable! Discovered %s switches and %s links.", len(event.switches), len(event.adjacency))
    # Add your controller logic here

def start_controller():
    # Register to the TopologyStable event
    core.TopologyDiscovery.addListenerByName("TopologyStable", _handle_TopologyStable)

def launch():
    # Disable logs from openflow.discovery component
    logging.getLogger("openflow.discovery").setLevel(logging.CRITICAL)
    
    # Launch our custom topology discovery module
    topology_discovery.launch()
    
    # Wait for TopologyDiscovery to be registered before adding listeners
    core.call_when_ready(start_controller, ['TopologyDiscovery'])
