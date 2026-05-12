"""
Topology Discovery Module for POX
"""

from pox.core import core
from pox.lib.recoco import Timer
from pox.lib.revent import Event, EventMixin

log = core.getLogger()

class TopologyStable(Event):
    """
    Fired when the topology has stopped changing for a configured amount of time.
    """
    def __init__(self, adjacency, switches):
        super(TopologyStable, self).__init__()
        self.adjacency = adjacency
        self.switches = switches

class TopologyDiscovery(EventMixin):
    _eventMixin_events = set([TopologyStable])

    def __init__(self):
        EventMixin.__init__(self)
        self.adjacency = set()
        self.switches = set()
        self.timer = None
        self.stable_timeout = 20 # seconds of inactivity to consider the topology stable

        core.openflow.addListeners(self)
        core.openflow_discovery.addListeners(self)

    def _handle_ConnectionUp(self, event):
        self.switches.add(event.dpid)
        self._reset_timer()

    def _handle_ConnectionDown(self, event):
        if event.dpid in self.switches:
            self.switches.remove(event.dpid)
        self._reset_timer()

    def _handle_LinkEvent(self, event):
        link = event.link
        if event.added:
            self.adjacency.add((link.dpid1, link.port1, link.dpid2, link.port2))
        elif event.removed:
            if (link.dpid1, link.port1, link.dpid2, link.port2) in self.adjacency:
                self.adjacency.remove((link.dpid1, link.port1, link.dpid2, link.port2))
        
        self._reset_timer()
        
    def _reset_timer(self):
        if self.timer is not None:
            self.timer.cancel()
        
        self.timer = Timer(self.stable_timeout, self._show_graph, recurring=False)

    def _show_graph(self):
        log.info("=========================================================")
        log.info("Topology is STABLE. Current Graph:")
        log.info("---------------------------------------------------------")
        log.info("Nodes (Switches):")
        for s in sorted(list(self.switches)):
            log.info("  - Switch DPID: %s", s)
        
        log.info("Edges (Links):")
        printed_links = set()
        for dpid1, port1, dpid2, port2 in sorted(list(self.adjacency)):
            link_repr = tuple(sorted(((dpid1, port1), (dpid2, port2))))
            if link_repr not in printed_links:
                log.info("  - Switch DPID: %s (port %s) <---> Switch DPID: %s (port %s)", dpid1, port1, dpid2, port2)
                printed_links.add(link_repr)
        log.info("=========================================================")
        
        # Notify the controller that the topology is stable
        self.raiseEventNoErrors(TopologyStable, self.adjacency, self.switches)

def launch():
    def start_discovery():
        core.registerNew(TopologyDiscovery)
        log.info("TopologyDiscovery module registered.")
        
    # Wait for dependencies before starting
    core.call_when_ready(start_discovery, ['openflow', 'openflow_discovery'])
