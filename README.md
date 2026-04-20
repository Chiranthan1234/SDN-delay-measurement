# SDN Network Delay Measurement Tool

> **Project #15 — SDN Mininet based Simulation | Orange Problem**
> Measure and analyze latency between hosts using OpenFlow and Ryu.

---

## Problem Statement

Design an SDN-based network delay measurement system using Mininet and a Ryu OpenFlow 1.3 controller. The system must:
- Use **ping** for delay measurement across network paths
- **Record RTT values** per packet and per path
- **Compare latency across two distinct paths** (fast vs. slow)
- **Analyze delay variations** (jitter, mdev, min/avg/max)

---

## Topology

```
              ┌──── s2 ────┐   PATH 1 — Fast  (~5 ms / link)
  h1 ── s1 ──┤             ├── s4 ── h3
  h2 ──/     └──── s3 ────┘         h4
                   PATH 2 — Slow (~20 ms / link)

Hosts:    h1 10.0.0.1   h2 10.0.0.2   h3 10.0.0.3   h4 10.0.0.4
Switches: s1 (ingress)  s2 (fast relay)  s3 (slow relay)  s4 (egress)
```

| Component | Detail |
|-----------|--------|
| Emulator | Mininet with TCLink (traffic control links) |
| Controller | Ryu OpenFlow 1.3 |
| Path 1 delay | 5 ms/link → ~20 ms RTT end-to-end |
| Path 2 delay | 20 ms/link → ~80 ms RTT end-to-end |
| Bandwidth | 100 Mbps on all links |

---

## File Structure

```
sdn-delay-tool/
├── topology.py        # Mininet dual-path topology
├── controller.py      # Ryu SDN controller (OpenFlow 1.3)
├── measure_delay.py   # Automated 4-scenario measurement script
├── analyze_delay.py   # Analysis & visualization (matplotlib)
├── run_demo.sh        # One-command demo runner
└── README.md
```

---

## Prerequisites

```bash
# Ubuntu 20.04 / 22.04 recommended
sudo apt-get update
sudo apt-get install -y mininet python3-pip curl

pip3 install ryu eventlet webob matplotlib numpy
```

---

## Execution Steps

### Step 1 — Start the Ryu Controller (Terminal 1)

```bash
cd sdn-delay-tool
ryu-manager controller.py
```

Expected output:
```
[INFO]  SDN Delay Measurement Controller – STARTED
[INFO]  OpenFlow 1.3 | REST API on :8080
[INFO]  Active path: PATH 1 (fast via s2)
```

---

### Step 2 — Run the Full Automated Demo (Terminal 2)

```bash
sudo python3 measure_delay.py
```

This runs **four scenarios** automatically:

| # | Scenario | What it tests |
|---|----------|---------------|
| 1 | Baseline ping | h1 → h3, h2 → h4 connectivity & RTT |
| 2 | Multi-host comparison | All 4 host pairs; compares RTT spread |
| 3 | Jitter analysis | 50-packet burst + 100-packet extended |
| 4 | Path comparison | Fast path (s2) vs. forced slow path (s3) |

After all tests, the Mininet CLI opens for manual exploration.

---

### Step 3 — Manual Testing in Mininet CLI

Inside the CLI that opens after `measure_delay.py`:

```bash
# Basic RTT test
h1 ping -c 20 h3

# Jitter test (fast packets)
h1 ping -c 50 -i 0.1 h3

# Extended delay variation
h1 ping -c 100 -i 0.2 h3

# Throughput measurement
h3 iperf -s &
h1 iperf -c 10.0.0.3 -t 10

# View flow table on switch s1
s1 ovs-ofctl dump-flows s1 -O OpenFlow13

# View all switches
for s in s1 s2 s3 s4; do echo "=== $s ==="; $s ovs-ofctl dump-flows $s -O OpenFlow13; done

# Cross-host RTT
h2 ping -c 10 h4
h1 ping -c 10 h4
```

---

### Step 4 — Generate Analysis Graphs

```bash
python3 analyze_delay.py
```

Produces `delay_analysis.png` with:
- RTT Timeline (all scenarios)
- RTT Distribution histogram
- Min/Avg/Max grouped bar chart
- Jitter per-packet variation
- Summary statistics table

---

### Step 5 — Controller REST API

While the controller is running:

```bash
# View controller statistics (flows installed, MAC table, packet_in count)
curl http://127.0.0.1:8080/stats

# Switch to slow path (PATH 2 via s3) and remeasure
curl -X POST http://127.0.0.1:8080/set_path/2
h1 ping -c 20 h3     # (run inside Mininet CLI — should show ~80ms RTT)

# Switch back to fast path
curl -X POST http://127.0.0.1:8080/set_path/1
h1 ping -c 20 h3     # (should show ~20ms RTT again)

# Clear all controller state
curl -X POST http://127.0.0.1:8080/clear
```

---

### Step 6 — One-Command Demo (for teacher)

```bash
sudo bash run_demo.sh
```

This automatically:
1. Cleans any old Mininet state
2. Starts Ryu controller in background
3. Runs all 4 measurement scenarios
4. Generates analysis graph
5. Shows REST API output

---

## Expected Output

### Scenario 4 – Path Comparison (typical results)

```
PATH 1 (fast via s2):  avg RTT ≈  22 ms
PATH 2 (slow via s3):  avg RTT ≈  82 ms
Δ Overhead of slow path: +60 ms  (~272% slower)
```

### Flow Table (s1 after learning)

```
cookie=0x0, priority=20, ip,in_port=1,nw_src=10.0.0.1,nw_dst=10.0.0.3
    actions=output:3
cookie=0x0, priority=20, ip,in_port=3,nw_src=10.0.0.3,nw_dst=10.0.0.1
    actions=output:1
cookie=0x0, priority=0
    actions=CONTROLLER:65535
```

---

## SDN Concepts Demonstrated

| Concept | Where |
|---------|-------|
| Controller–switch interaction | `controller.py` → `switch_features_handler`, `packet_in_handler` |
| Flow rule match+action | `_install_flow()` with `OFPMatch` and `OFPActionOutput` |
| Priority & timeouts | `priority=20`, `idle_timeout=30`, `hard_timeout=60` |
| Table-miss entry | `priority=0`, `OFPP_CONTROLLER` action |
| Path switching | `run_demo.sh` / REST `/set_path/1` and `/set_path/2` |
| Packet_in events | Logged to `controller.log` and `/stats` REST endpoint |
| Flow statistics | `ovs-ofctl dump-flows` + controller log |
| Delay measurement | `ping` RTT with parsing in `measure_delay.py` |
| Jitter analysis | Per-packet `|Δ RTT|` in `analyze_delay.py` |

---

## Test Scenarios (Allowed vs. Blocked)

### Scenario A – Allowed traffic (PATH 1 open)
```bash
# Both paths open → traffic flows freely
h1 ping -c 5 h3        # expect ~20 ms RTT
```

### Scenario B – PATH 1 blocked (forced reroute)
```bash
# Inside Mininet CLI — manually block s2:
s2 ovs-ofctl add-flow s2 "priority=200,actions=drop" -O OpenFlow13
s1 ovs-ofctl del-flows s1 -O OpenFlow13    # clear learning
h1 ping -c 5 h3        # expect ~80 ms RTT (rerouted via s3)
```

### Scenario C – Failure recovery
```bash
# Restore s2
s2 ovs-ofctl del-flows s2 -O OpenFlow13
s1 ovs-ofctl del-flows s1 -O OpenFlow13
h1 ping -c 5 h3        # expect ~20 ms RTT again
```

---

## Output Files

| File | Description |
|------|-------------|
| `delay_results.json` | All RTT values per scenario (JSON) |
| `delay_results.csv`  | Flat RTT table for Excel/Sheets |
| `delay_analysis.png` | 5-panel visualization dashboard |
| `controller.log`     | Timestamped controller event log |
| `ryu.log`            | Ryu startup and error log |

---

## References

1. Mininet Documentation – http://mininet.org/
2. Ryu SDN Framework – https://ryu.readthedocs.io/
3. OpenFlow 1.3 Specification – https://opennetworking.org/
4. TCLink (Traffic Control) in Mininet – https://github.com/mininet/mininet/wiki/Introduction-to-Mininet
5. OVS OpenFlow Commands – http://openvswitch.org/support/dist-docs/ovs-ofctl.8.txt
