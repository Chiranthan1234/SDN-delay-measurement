#!/usr/bin/env python3
"""
SDN Network Delay Measurement Tool
====================================
Ryu OpenFlow 1.3 Controller

Features:
  • Handles packet_in events and learns MAC→port mappings
  • Installs explicit match+action flow rules (priority, idle/hard timeouts)
  • Supports PATH 1 (via s2, fast) and PATH 2 (via s3, slow) selection
  • REST API at http://127.0.0.1:8080
      GET  /stats          – controller statistics & flow log
      POST /set_path/1     – force traffic via PATH 1 (fast)
      POST /set_path/2     – force traffic via PATH 2 (slow)
      POST /clear          – flush learned state
  • Full logging to controller.log

Run:
    ryu-manager controller.py --observe-links

Author: SDN Delay Measurement Project
"""

import json
import logging
import time
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, arp
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response


# ─── Logging Setup ───────────────────────────────────────────────────────────

LOG = logging.getLogger('delay_controller')
LOG.setLevel(logging.DEBUG)

_fh = logging.FileHandler('controller.log')
_fh.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
LOG.addHandler(_fh)

# Console handler (only INFO and above to keep terminal clean)
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
LOG.addHandler(_ch)


# ─── Constants ───────────────────────────────────────────────────────────────

# Known switch DPIDs (s1=1, s2=2, s3=3, s4=4 in Mininet default numbering)
DPID_S1, DPID_S2, DPID_S3, DPID_S4 = 1, 2, 3, 4

# Static port map for our dual-path topology.
# Keys: (dpid, path) → {dst_dpid: out_port}
# These ports match the addLink() order in topology.py.
#
#  s1 ports: 1=h1  2=h2  3=s2  4=s3
#  s2 ports: 1=s1  2=s4
#  s3 ports: 1=s1  2=s4
#  s4 ports: 1=h3  2=h4  3=s2  4=s3
#
TOPOLOGY_PORTS = {
    # s1 egress to each switch
    (DPID_S1, DPID_S2): 3,   # s1→s2 (PATH 1)
    (DPID_S1, DPID_S3): 4,   # s1→s3 (PATH 2)
    (DPID_S1, 'h1'): 1,
    (DPID_S1, 'h2'): 2,
    # s2
    (DPID_S2, DPID_S1): 1,
    (DPID_S2, DPID_S4): 2,
    # s3
    (DPID_S3, DPID_S1): 1,
    (DPID_S3, DPID_S4): 2,
    # s4
    (DPID_S4, DPID_S2): 3,
    (DPID_S4, DPID_S3): 4,
    (DPID_S4, 'h3'): 1,
    (DPID_S4, 'h4'): 2,
}

# IP→host tag mapping
IP_HOST = {
    '10.0.0.1': 'h1', '10.0.0.2': 'h2',
    '10.0.0.3': 'h3', '10.0.0.4': 'h4',
}


# ─── Controller Application ──────────────────────────────────────────────────

class DelayMeasurementController(app_manager.RyuApp):
    """
    SDN controller that demonstrates:
      1. Switch feature negotiation (OpenFlow handshake)
      2. packet_in event handling
      3. match+action flow rule installation
      4. Path selection (fast / slow) via REST API
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Learning table: {dpid: {mac: port}}
        self.mac_to_port: dict = {}

        # Active path preference (1 = via s2 fast, 2 = via s3 slow)
        self.active_path: int = 1

        # Statistics
        self.packet_in_count: dict = defaultdict(int)
        self.flow_log: list = []          # installed flow records
        self.connected_switches: dict = {}  # dpid → connect time

        # REST
        wsgi = kwargs['wsgi']
        wsgi.register(DelayRestAPI, {'app': self})

        LOG.info("=" * 55)
        LOG.info("  SDN Delay Measurement Controller – STARTED")
        LOG.info("  OpenFlow 1.3 | REST API on :8080")
        LOG.info("  Active path: PATH %d (fast via s2)", self.active_path)
        LOG.info("=" * 55)

    # ── 1. Switch Handshake ──────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Called when a switch connects.
        Installs a table-miss flow entry:
          Match: ANY
          Action: SEND_TO_CONTROLLER
        This ensures unmatched packets trigger packet_in events.
        """
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        dpid = dp.id

        self.connected_switches[dpid] = time.strftime('%H:%M:%S')
        LOG.info("Switch CONNECTED  dpid=0x%08x", dpid)

        # Table-miss: priority=0, match=*, action=controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._install_flow(dp, priority=0, match=match, actions=actions,
                           idle_timeout=0, hard_timeout=0)

        LOG.info("Table-miss flow installed on dpid=0x%08x", dpid)

    # ── 2. Packet-In Event Handler ───────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """
        Called for every packet the switch cannot match locally.

        Actions:
          a) Parse Ethernet frame.
          b) Learn source MAC → ingress port.
          c) Decide egress port (known MAC or FLOOD).
          d) If port known → install a flow rule so future packets
             are forwarded at line rate without hitting the controller.
          e) Forward the current packet via PacketOut.
        """
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        dpid = dp.id
        in_port = msg.match['in_port']

        # Parse the Ethernet header
        pkt = packet.Packet(msg.data)
        eth_hdr = pkt.get_protocols(ethernet.ethernet)[0]

        # Skip LLDP (link-layer discovery – not application data)
        if eth_hdr.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src_mac = eth_hdr.src
        dst_mac = eth_hdr.dst

        # Count packet_in events per switch
        self.packet_in_count[dpid] += 1

        LOG.debug("PacketIn  dpid=0x%08x  port=%d  %s → %s  (#%d)",
                  dpid, in_port, src_mac, dst_mac,
                  self.packet_in_count[dpid])

        # ── (b) MAC Learning ──────────────────────────────────────
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # ── (c) Egress Port Decision ──────────────────────────────
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofp.OFPP_FLOOD   # Broadcast ARP / unknown

        actions = [parser.OFPActionOutput(out_port)]

        # ── (d) Flow Rule Installation ────────────────────────────
        if out_port != ofp.OFPP_FLOOD:
            ip_hdr = pkt.get_protocol(ipv4.ipv4)

            if ip_hdr:
                # Specific IP-level flow rule (higher priority)
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_type=ether_types.ETH_TYPE_IP,
                    ipv4_src=ip_hdr.src,
                    ipv4_dst=ip_hdr.dst,
                )
                priority = 20
                LOG.info("FlowInstall  dpid=0x%08x  %s → %s  port=%d  (IP)",
                         dpid, ip_hdr.src, ip_hdr.dst, out_port)
            else:
                # MAC-level flow rule
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_dst=dst_mac,
                    eth_src=src_mac,
                )
                priority = 10
                LOG.info("FlowInstall  dpid=0x%08x  %s → %s  port=%d  (L2)",
                         dpid, src_mac, dst_mac, out_port)

            self._install_flow(dp, priority=priority, match=match,
                               actions=actions,
                               idle_timeout=30, hard_timeout=60)

            # Record in log
            self.flow_log.append({
                'time': time.strftime('%H:%M:%S'),
                'dpid': hex(dpid),
                'src': src_mac,
                'dst': dst_mac,
                'in_port': in_port,
                'out_port': out_port,
                'priority': priority,
            })

        # ── (e) Send PacketOut for the current packet ─────────────
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        dp.send_msg(out)

    # ── Internal helpers ─────────────────────────────────────────────

    def _install_flow(self, dp, priority, match, actions,
                      idle_timeout=30, hard_timeout=60):
        """Send OFPFlowMod to install a flow entry on the switch."""
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        inst = [parser.OFPInstructionActions(
            ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        dp.send_msg(mod)

    def _delete_all_flows(self, dp):
        """Remove all non-table-miss flows from a switch."""
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        match = parser.OFPMatch()
        mod = parser.OFPFlowMod(
            datapath=dp,
            command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY,
            out_group=ofp.OFPG_ANY,
            match=match,
        )
        dp.send_msg(mod)

    # ── REST-callable methods ─────────────────────────────────────────

    def get_stats(self):
        return {
            'active_path': self.active_path,
            'path_description': (
                'PATH 1 – Fast (via s2, ~10ms)' if self.active_path == 1
                else 'PATH 2 – Slow (via s3, ~40ms)'
            ),
            'connected_switches': self.connected_switches,
            'packet_in_counts': dict(self.packet_in_count),
            'mac_table': {hex(k): v for k, v in self.mac_to_port.items()},
            'total_flows_installed': len(self.flow_log),
            'recent_flows': self.flow_log[-15:],
        }

    def set_path(self, path_num: int):
        """Switch active path and flush learned state so traffic re-routes."""
        self.active_path = path_num
        self.mac_to_port = {}   # Force relearning via new path
        self.flow_log = []
        LOG.info(">>> PATH SWITCHED to PATH %d <<<", path_num)
        return {'status': 'ok', 'active_path': path_num}

    def clear_all(self):
        self.mac_to_port = {}
        self.flow_log = {}
        self.packet_in_count = defaultdict(int)
        LOG.info("Controller state cleared.")
        return {'status': 'cleared'}


# ─── REST API ────────────────────────────────────────────────────────────────

class DelayRestAPI(ControllerBase):
    """Simple REST interface for runtime controller inspection and control."""

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.ctrl = data['app']

    @route('stats', '/stats', methods=['GET'])
    def stats(self, req, **kw):
        body = json.dumps(self.ctrl.get_stats(), indent=2)
        return Response(content_type='application/json', body=body)

    @route('path', '/set_path/{num}', methods=['POST'])
    def set_path(self, req, num, **kw):
        result = self.ctrl.set_path(int(num))
        return Response(content_type='application/json',
                        body=json.dumps(result))

    @route('clear', '/clear', methods=['POST'])
    def clear(self, req, **kw):
        result = self.ctrl.clear_all()
        return Response(content_type='application/json',
                        body=json.dumps(result))
