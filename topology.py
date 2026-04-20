#!/usr/bin/env python3
"""
SDN Network Delay Measurement Tool
===================================
Mininet Topology: Dual-Path Network

Topology Diagram:
                   PATH 1 (Fast ~5ms/link)
              ┌──── s2 ────┐
  h1 ── s1 ──┤             ├── s4 ── h3
  h2 ──/     └──── s3 ────┘         h4
                   PATH 2 (Slow ~20ms/link)

Hosts:   h1(10.0.0.1), h2(10.0.0.2), h3(10.0.0.3), h4(10.0.0.4)
Switches: s1 (ingress), s2 (fast relay), s3 (slow relay), s4 (egress)

Author: SDN Delay Measurement Project
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.topo import Topo


# ─── Topology Definition ─────────────────────────────────────────────────────

class DualPathTopo(Topo):
    """
    Dual-path topology enabling path-based delay comparison.
    Path 1 (via s2): low delay – simulates a fast backbone link.
    Path 2 (via s3): high delay – simulates a slower/distant route.
    """

    def build(self, path1_delay='5ms', path2_delay='20ms',
              host_delay='1ms', bw=100):
        """
        Build the topology.

        Args:
            path1_delay: Propagation delay for the fast path (s1↔s2↔s4).
            path2_delay: Propagation delay for the slow path (s1↔s3↔s4).
            host_delay:  Delay on host access links.
            bw:          Bandwidth (Mbps) on all links.
        """

        # ── Hosts ──────────────────────────────────────────────────
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')
        h4 = self.addHost('h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04')

        # ── Switches ───────────────────────────────────────────────
        s1 = self.addSwitch('s1', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s2 = self.addSwitch('s2', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s3 = self.addSwitch('s3', cls=OVSKernelSwitch, protocols='OpenFlow13')
        s4 = self.addSwitch('s4', cls=OVSKernelSwitch, protocols='OpenFlow13')

        # ── Host access links (low latency) ────────────────────────
        self.addLink(h1, s1, bw=bw, delay=host_delay)   # h1-eth0 ↔ s1-eth1
        self.addLink(h2, s1, bw=bw, delay=host_delay)   # h2-eth0 ↔ s1-eth2
        self.addLink(h3, s4, bw=bw, delay=host_delay)   # h3-eth0 ↔ s4-eth1
        self.addLink(h4, s4, bw=bw, delay=host_delay)   # h4-eth0 ↔ s4-eth2

        # ── PATH 1: Fast path  s1 ─── s2 ─── s4 ───────────────────
        self.addLink(s1, s2, bw=bw, delay=path1_delay)  # s1-eth3 ↔ s2-eth1
        self.addLink(s2, s4, bw=bw, delay=path1_delay)  # s2-eth2 ↔ s4-eth3

        # ── PATH 2: Slow path  s1 ─── s3 ─── s4 ───────────────────
        self.addLink(s1, s3, bw=bw, delay=path2_delay)  # s1-eth4 ↔ s3-eth1
        self.addLink(s3, s4, bw=bw, delay=path2_delay)  # s3-eth2 ↔ s4-eth4


# ─── Network Factory ──────────────────────────────────────────────────────────

def create_network(controller_ip='127.0.0.1', controller_port=6633):
    """Create and return the Mininet network (controller not started yet)."""
    topo = DualPathTopo()
    net = Mininet(
        topo=topo,
        controller=None,     # Added manually below for flexibility
        link=TCLink,
        autoSetMacs=False,
        autoStaticArp=False
    )
    net.addController(
        'c0',
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
    )
    return net


# ─── Entry Point ──────────────────────────────────────────────────────────────

def run():
    setLogLevel('info')

    info('\n' + '=' * 62 + '\n')
    info('  SDN Network Delay Measurement Tool – Mininet Topology\n')
    info('  IMPORTANT: Start Ryu controller FIRST in another terminal:\n')
    info('     ryu-manager controller.py\n')
    info('=' * 62 + '\n\n')

    net = create_network()
    net.start()

    info('\n*** Network ready!\n')
    info('*** Hosts:   h1(10.0.0.1)  h2(10.0.0.2)  '
         'h3(10.0.0.3)  h4(10.0.0.4)\n')
    info('*** Switches: s1  s2  s3  s4\n')
    info('*** Path 1 (Fast) : s1 → s2 → s4  [~5 ms/link]\n')
    info('*** Path 2 (Slow) : s1 → s3 → s4  [~20 ms/link]\n\n')
    info('*** Quick test: h1 ping -c 5 h3\n\n')

    CLI(net)
    net.stop()
    info('*** Network stopped.\n')


if __name__ == '__main__':
    run()
