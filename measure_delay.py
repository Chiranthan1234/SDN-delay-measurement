#!/usr/bin/env python3
"""
SDN Network Delay Measurement Tool
====================================
Automated Measurement Script

Runs four test scenarios inside Mininet:
  Scenario 1 – Baseline ping test            (h1 → h3)
  Scenario 2 – Multi-host latency comparison  (h1→h3, h2→h4, h1→h4)
  Scenario 3 – Jitter / delay variation       (fast burst ping)
  Scenario 4 – Path comparison via OVS rules  (block s2 → force s3)

Outputs:
  delay_results.json   – raw RTT data for all tests
  delay_results.csv    – flat RTT table for spreadsheet import

Run (as root, after starting ryu-manager controller.py):
    sudo python3 measure_delay.py

Author: SDN Delay Measurement Project
"""

import csv
import json
import os
import re
import subprocess
import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from topology import create_network


# ─── RTT Parsing ─────────────────────────────────────────────────────────────

def parse_ping(raw: str) -> dict:
    """
    Parse 'ping' command output.
    Returns dict with keys: rtts, min, avg, max, jitter, count, loss_pct
    """
    rtts = []
    for line in raw.splitlines():
        m = re.search(r'time=(\d+\.?\d*)\s*ms', line)
        if m:
            rtts.append(float(m.group(1)))

    # Parse summary line:  rtt min/avg/max/mdev = x/x/x/x ms
    stats = {'rtts': rtts, 'count': len(rtts)}
    summary = re.search(
        r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', raw)
    if summary:
        stats['min']    = float(summary.group(1))
        stats['avg']    = float(summary.group(2))
        stats['max']    = float(summary.group(3))
        stats['mdev']   = float(summary.group(4))   # standard deviation
    elif rtts:
        avg = sum(rtts) / len(rtts)
        stats['min']  = min(rtts)
        stats['avg']  = avg
        stats['max']  = max(rtts)
        stats['mdev'] = sum(abs(r - avg) for r in rtts) / len(rtts)

    # Packet loss
    loss_m = re.search(r'(\d+)% packet loss', raw)
    stats['loss_pct'] = int(loss_m.group(1)) if loss_m else 0

    # Jitter: mean of consecutive RTT differences
    if len(rtts) > 1:
        diffs = [abs(rtts[i] - rtts[i - 1]) for i in range(1, len(rtts))]
        stats['jitter'] = sum(diffs) / len(diffs)
    else:
        stats['jitter'] = 0.0

    return stats


# ─── Helpers ─────────────────────────────────────────────────────────────────

def ping_test(src, dst, count=20, interval=0.5, label='') -> dict:
    """Run ping from src host to dst host and return parsed stats."""
    tag = label or f"{src.name}→{dst.name}"
    print(f"    ▶ [{tag}]  ping -c {count} -i {interval}  {dst.IP()}")
    raw = src.cmd(f'ping -c {count} -i {interval} {dst.IP()}')
    s = parse_ping(raw)
    if 'avg' in s:
        print(f"      RTT  min={s['min']:.2f}  avg={s['avg']:.2f}  "
              f"max={s['max']:.2f}  jitter={s['jitter']:.2f}  "
              f"loss={s['loss_pct']}%  [ms]")
    else:
        print(f"      ⚠ No RTT data – is controller running?")
    return s


def block_switch_port(sw, port: int):
    """Add a high-priority OpenFlow DROP rule on a switch port (blocks path)."""
    sw.cmd(f'ovs-ofctl add-flow {sw.name} '
           f'"priority=200,in_port={port},actions=drop" -O OpenFlow13')


def clear_switch_flows(sw):
    """Remove all non-default flows from a switch."""
    sw.cmd(f'ovs-ofctl del-flows {sw.name} -O OpenFlow13')


def dump_flows(sw) -> str:
    """Return human-readable flow table of a switch."""
    return sw.cmd(f'ovs-ofctl dump-flows {sw.name} -O OpenFlow13')


def banner(title: str):
    print('\n' + '─' * 60)
    print(f'  {title}')
    print('─' * 60)


# ─── Main Measurement Routine ─────────────────────────────────────────────────

def run_measurements():
    setLogLevel('warning')   # Suppress Mininet noise; our prints are enough

    print('\n' + '=' * 60)
    print('  SDN Network Delay Measurement Tool')
    print('  Automated RTT Measurement & Analysis')
    print('=' * 60)
    print('\n  ⚠  Prerequisite: ryu-manager controller.py must be running\n')

    # ── Start Network ────────────────────────────────────────────────
    print('[SETUP] Starting Mininet …')
    net = create_network()
    net.start()
    time.sleep(4)   # Allow controller to connect and install table-miss

    h1 = net.get('h1')
    h2 = net.get('h2')
    h3 = net.get('h3')
    h4 = net.get('h4')
    s1 = net.get('s1')
    s2 = net.get('s2')
    s3 = net.get('s3')
    s4 = net.get('s4')

    all_results: dict = {}

    # ════════════════════════════════════════════════════════════════
    # SCENARIO 1 – Baseline Connectivity & Latency
    # ════════════════════════════════════════════════════════════════
    banner('SCENARIO 1 – Baseline Connectivity & Latency')
    print('  Warming up ARP tables …')
    h1.cmd(f'ping -c 3 -W 2 {h3.IP()} > /dev/null 2>&1')
    h2.cmd(f'ping -c 3 -W 2 {h4.IP()} > /dev/null 2>&1')
    time.sleep(1)

    all_results['s1_h1_to_h3'] = ping_test(h1, h3, count=20, label='Scenario1 h1→h3')
    all_results['s1_h2_to_h4'] = ping_test(h2, h4, count=20, label='Scenario1 h2→h4')

    print('\n  [Flow table on s1 after Scenario 1]')
    print(dump_flows(s1))

    # ════════════════════════════════════════════════════════════════
    # SCENARIO 2 – Multi-Host Latency Comparison
    # ════════════════════════════════════════════════════════════════
    banner('SCENARIO 2 – Multi-Host Latency Comparison')
    print('  Testing all host-pair combinations …\n')

    pairs = [
        (h1, h3, 'h1→h3'),
        (h1, h4, 'h1→h4'),
        (h2, h3, 'h2→h3'),
        (h2, h4, 'h2→h4'),
    ]
    for src, dst, lbl in pairs:
        key = f's2_{lbl.replace("→", "_to_")}'
        all_results[key] = ping_test(src, dst, count=15, label=f'Scenario2 {lbl}')
        time.sleep(0.5)

    # ════════════════════════════════════════════════════════════════
    # SCENARIO 3 – Jitter / Delay Variation Analysis
    # ════════════════════════════════════════════════════════════════
    banner('SCENARIO 3 – Jitter / Delay Variation Analysis')
    print('  High-frequency ping to measure inter-packet delay variation …\n')

    # Fast burst
    print('  Test 3.1 – Fast burst (50 pkts, 100ms interval)')
    raw_burst = h1.cmd(f'ping -c 50 -i 0.1 {h3.IP()}')
    s3a = parse_ping(raw_burst)
    all_results['s3_burst_h1_h3'] = s3a
    if 'avg' in s3a:
        print(f'    Jitter={s3a["jitter"]:.2f}ms  mdev={s3a.get("mdev",0):.2f}ms  '
              f'over {s3a["count"]} packets')

    # Long sequence
    print('\n  Test 3.2 – Extended sequence (100 pkts, 200ms interval)')
    raw_long = h1.cmd(f'ping -c 100 -i 0.2 {h3.IP()}')
    s3b = parse_ping(raw_long)
    all_results['s3_extended_h1_h3'] = s3b
    if 'avg' in s3b:
        print(f'    Jitter={s3b["jitter"]:.2f}ms  mdev={s3b.get("mdev",0):.2f}ms  '
              f'over {s3b["count"]} packets')

    # ════════════════════════════════════════════════════════════════
    # SCENARIO 4 – Path Comparison (Fast Path vs Slow Path)
    # ════════════════════════════════════════════════════════════════
    banner('SCENARIO 4 – Path Comparison (Fast vs Slow)')

    # Step 4a: Measure current (PATH 1 – fast, via s2) baseline
    print('\n  Step 4a – Clear flows, let traffic settle on PATH 1 (via s2) …')
    for sw in [s1, s2, s3, s4]:
        clear_switch_flows(sw)
    time.sleep(2)
    h1.cmd(f'ping -c 3 -W 2 {h3.IP()} > /dev/null 2>&1')  # trigger ARP+learning
    time.sleep(1)

    print('\n  Measuring PATH 1 (default fast path, s1→s2→s4) …')
    all_results['s4_path1_fast'] = ping_test(h1, h3, count=30, label='Scenario4 PATH1 fast')

    print('\n  Flow table on s1 (PATH 1):')
    print(dump_flows(s1))

    # Step 4b: Block PATH 1 (block port on s2 that leads to s4)
    # and block the port on s4 that comes from s2, forcing traffic via s3
    print('\n  Step 4b – Blocking PATH 1 (adding drop rules on s2 and s4) …')
    # Drop everything on s2 → effectively cut that path
    s2.cmd('ovs-ofctl add-flow s2 "priority=200,actions=drop" -O OpenFlow13')
    # Also drop on s4 port connected to s2
    s4.cmd('ovs-ofctl add-flow s4 "priority=200,in_port=3,actions=drop" -O OpenFlow13')

    # Clear learning so controller re-learns via s3
    for sw in [s1, s3, s4]:
        clear_switch_flows(sw)
    time.sleep(2)
    h1.cmd(f'ping -c 3 -W 3 {h3.IP()} > /dev/null 2>&1')
    time.sleep(1)

    print('\n  Measuring PATH 2 (slow path forced, s1→s3→s4) …')
    all_results['s4_path2_slow'] = ping_test(h1, h3, count=30, label='Scenario4 PATH2 slow')

    print('\n  Flow table on s1 (PATH 2):')
    print(dump_flows(s1))

    # ────────────────────────────────────────────────────────────────
    # Restore normal state
    for sw in [s1, s2, s3, s4]:
        clear_switch_flows(sw)
    time.sleep(1)

    # ════════════════════════════════════════════════════════════════
    # SUMMARY REPORT
    # ════════════════════════════════════════════════════════════════
    banner('MEASUREMENT SUMMARY')
    print(f"\n  {'Test':<32} {'Min':>7} {'Avg':>7} {'Max':>7} "
          f"{'Jitter':>8} {'Loss':>6} {'N':>5}")
    print('  ' + '─' * 76)
    for name, data in all_results.items():
        if 'avg' in data:
            print(f"  {name.replace('_',' '):<32} "
                  f"{data['min']:>6.1f}  "
                  f"{data['avg']:>6.1f}  "
                  f"{data['max']:>6.1f}  "
                  f"{data['jitter']:>7.1f}  "
                  f"{data.get('loss_pct',0):>5}%  "
                  f"{data['count']:>5}")

    # Path comparison callout
    if 's4_path1_fast' in all_results and 's4_path2_slow' in all_results:
        p1 = all_results['s4_path1_fast'].get('avg', 0)
        p2 = all_results['s4_path2_slow'].get('avg', 0)
        print(f"\n  PATH 1 (fast)  avg RTT : {p1:.2f} ms")
        print(f"  PATH 2 (slow)  avg RTT : {p2:.2f} ms")
        if p1 and p2:
            print(f"  Delay overhead of slow path: +{p2-p1:.2f} ms  "
                  f"({(p2/p1-1)*100:.0f}% slower)")

    # ── Save results ──────────────────────────────────────────────
    with open('delay_results.json', 'w') as f:
        json.dump(all_results, f, indent=2)
    print('\n  ✔ Results saved to delay_results.json')

    # CSV export (flat RTT list)
    with open('delay_results.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['scenario', 'packet_num', 'rtt_ms'])
        for name, data in all_results.items():
            for i, rtt in enumerate(data.get('rtts', []), start=1):
                w.writerow([name, i, rtt])
    print('  ✔ RTT data saved to delay_results.csv')
    print('\n  ▶ Run:  python3 analyze_delay.py   to generate graphs\n')

    # ── Open CLI for manual exploration ──────────────────────────
    print('  Opening Mininet CLI – useful commands:')
    print('    h1 ping -c 20 h3                          ← basic RTT test')
    print('    h1 ping -c 50 -i 0.1 h3                   ← jitter test')
    print('    h1 iperf -s &; h3 iperf -c 10.0.0.1       ← throughput')
    print('    s1 ovs-ofctl dump-flows s1 -O OpenFlow13  ← flow table')
    print('    curl http://127.0.0.1:8080/stats           ← controller REST\n')

    CLI(net)
    net.stop()
    print('\n  Network stopped. Measurement complete.')


if __name__ == '__main__':
    run_measurements()
