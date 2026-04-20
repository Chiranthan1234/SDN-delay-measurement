#!/usr/bin/env python3
"""
SDN Network Delay Measurement Tool
====================================
Analysis & Visualization Script

Reads delay_results.json and generates:
  delay_analysis.png  – 5-panel analysis dashboard

Panels:
  1. RTT Timeline     – per-packet RTT over time for all tests
  2. RTT Distribution – histogram showing spread
  3. Min/Avg/Max bars – grouped bar chart comparing tests
  4. Jitter Series    – per-packet RTT variation (|Δ RTT|)
  5. Summary Table    – numeric stats for all scenarios

Run:
    python3 analyze_delay.py

Author: SDN Delay Measurement Project
"""

import json
import os
import sys

import numpy as np


# ─── Data Loading ─────────────────────────────────────────────────────────────

def load_results(path='delay_results.json') -> dict:
    if not os.path.exists(path):
        print(f'  ✘ {path} not found.')
        print('    Run measure_delay.py first to collect data.')
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


# ─── Text Summary ─────────────────────────────────────────────────────────────

def print_summary(results: dict):
    print('\n' + '=' * 65)
    print('  SDN DELAY ANALYSIS REPORT')
    print('=' * 65)
    print(f"\n  {'Scenario':<32} {'Min':>6} {'Avg':>6} {'Max':>6} "
          f"{'Jitter':>8} {'mdev':>6} {'N':>5}")
    print('  ' + '─' * 65)

    avgs = {}
    for name, data in results.items():
        if 'avg' not in data:
            continue
        avgs[name] = data['avg']
        print(f"  {name.replace('_',' '):<32} "
              f"{data['min']:>5.1f}ms "
              f"{data['avg']:>5.1f}ms "
              f"{data['max']:>5.1f}ms "
              f"{data.get('jitter',0):>7.1f}ms "
              f"{data.get('mdev',0):>5.1f}ms "
              f"{data.get('count',0):>5}")

    if avgs:
        best  = min(avgs, key=avgs.get)
        worst = max(avgs, key=avgs.get)
        vals  = list(avgs.values())
        print('\n  ✓ Lowest  avg RTT:', f"{best} ({avgs[best]:.2f} ms)")
        print('  ✗ Highest avg RTT:', f"{worst} ({avgs[worst]:.2f} ms)")
        if len(vals) > 1:
            print(f'  Δ Range: {max(vals)-min(vals):.2f} ms')

    # Path comparison
    p1 = results.get('s4_path1_fast', {}).get('avg')
    p2 = results.get('s4_path2_slow', {}).get('avg')
    if p1 and p2:
        print('\n  PATH COMPARISON')
        print(f'    PATH 1 (fast via s2): {p1:.2f} ms avg RTT')
        print(f'    PATH 2 (slow via s3): {p2:.2f} ms avg RTT')
        print(f'    Δ Overhead: +{p2-p1:.2f} ms  ({(p2/p1-1)*100:.0f}% slower)')

    print()


# ─── Visualization ────────────────────────────────────────────────────────────

PALETTE = ['#00d4ff', '#ff6b35', '#7bc67e', '#ffd166', '#ef476f',
           '#a78bfa', '#fb923c', '#34d399']

BG_DARK  = '#0f0f1a'
BG_PANEL = '#1a1a2e'
BG_PANEL2 = '#16213e'
GRID_CLR = '#ffffff22'
SPINE_CLR = '#333355'


def style_ax(ax, title='', xlabel='', ylabel=''):
    ax.set_facecolor(BG_PANEL2)
    ax.tick_params(colors='#cccccc', labelsize=8)
    ax.set_title(title, color='white', fontsize=11, fontweight='bold', pad=8)
    ax.set_xlabel(xlabel, color='#aaaaaa', fontsize=9)
    ax.set_ylabel(ylabel, color='#aaaaaa', fontsize=9)
    ax.grid(True, color=GRID_CLR, linewidth=0.6)
    for sp in ax.spines.values():
        sp.set_edgecolor(SPINE_CLR)


def generate_plots(results: dict):
    try:
        import matplotlib
        matplotlib.use('Agg')          # headless – works without X server
        import matplotlib.pyplot as plt
        import matplotlib.gridspec as gridspec
        from matplotlib.ticker import MaxNLocator
    except ImportError:
        print('  ✘ matplotlib not installed. Run: pip3 install matplotlib')
        return

    valid = [(k, v) for k, v in results.items() if 'rtts' in v and v['rtts']]
    if not valid:
        print('  ✘ No RTT data to plot.')
        return

    fig = plt.figure(figsize=(18, 13), facecolor=BG_DARK)
    gs  = gridspec.GridSpec(3, 2, figure=fig, hspace=0.50, wspace=0.32,
                            top=0.93, bottom=0.06, left=0.06, right=0.97)

    # ── Panel 1: RTT Timeline (full width) ──────────────────────────
    ax1 = fig.add_subplot(gs[0, :])
    style_ax(ax1, 'RTT Timeline – All Scenarios',
             'Packet Number', 'RTT (ms)')
    for i, (name, data) in enumerate(valid):
        c = PALETTE[i % len(PALETTE)]
        rtts = data['rtts']
        ax1.plot(range(1, len(rtts) + 1), rtts, 'o-',
                 color=c, linewidth=1.4, markersize=2.5, alpha=0.8,
                 label=name.replace('_', ' '))
        ax1.axhline(np.mean(rtts), color=c, linestyle='--',
                    linewidth=0.8, alpha=0.45)
    ax1.legend(facecolor=BG_PANEL, labelcolor='white',
               fontsize=7.5, framealpha=0.9,
               ncol=min(4, len(valid)), loc='upper right')

    # ── Panel 2: RTT Histogram ──────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, 0])
    style_ax(ax2, 'RTT Distribution (Histogram)',
             'RTT (ms)', 'Frequency')
    for i, (name, data) in enumerate(valid[:4]):   # up to 4 series
        c = PALETTE[i % len(PALETTE)]
        ax2.hist(data['rtts'], bins=14, color=c, alpha=0.55,
                 label=name.replace('_', ' ')[:22],
                 edgecolor='none')
        ax2.axvline(np.mean(data['rtts']), color=c,
                    linewidth=1.6, linestyle='--')
    ax2.legend(facecolor=BG_PANEL, labelcolor='white',
               fontsize=7, framealpha=0.9)

    # ── Panel 3: Min / Avg / Max grouped bars ───────────────────────
    ax3 = fig.add_subplot(gs[1, 1])
    style_ax(ax3, 'Min / Avg / Max RTT per Scenario', 'Scenario', 'RTT (ms)')
    stat_tests = [(k, v) for k, v in results.items() if 'avg' in v]
    if stat_tests:
        labels = [k.replace('_', '\n')[:18] for k, _ in stat_tests]
        mins  = [v['min']  for _, v in stat_tests]
        avgs  = [v['avg']  for _, v in stat_tests]
        maxs  = [v['max']  for _, v in stat_tests]
        x = np.arange(len(stat_tests))
        w = 0.25
        ax3.bar(x - w, mins, w, color='#7bc67e', label='Min', alpha=0.85)
        ax3.bar(x,     avgs, w, color='#00d4ff', label='Avg', alpha=0.85)
        ax3.bar(x + w, maxs, w, color='#ef476f', label='Max', alpha=0.85)
        ax3.set_xticks(x)
        ax3.set_xticklabels(labels, fontsize=6.5, color='#cccccc')
        ax3.legend(facecolor=BG_PANEL, labelcolor='white',
                   fontsize=7.5, framealpha=0.9)
        ax3.yaxis.set_major_locator(MaxNLocator(integer=False, nbins=6))

    # ── Panel 4: Jitter Series ──────────────────────────────────────
    ax4 = fig.add_subplot(gs[2, 0])
    style_ax(ax4, 'Jitter – Per-Packet RTT Variation |Δ RTT|',
             'Packet Number', '|Δ RTT| (ms)')
    for i, (name, data) in enumerate(valid[:4]):
        rtts = data['rtts']
        if len(rtts) < 2:
            continue
        jitter_series = [abs(rtts[j] - rtts[j - 1]) for j in range(1, len(rtts))]
        ax4.plot(range(1, len(jitter_series) + 1), jitter_series,
                 '-', color=PALETTE[i % len(PALETTE)],
                 linewidth=1.1, alpha=0.75,
                 label=name.replace('_', ' ')[:22])
    ax4.legend(facecolor=BG_PANEL, labelcolor='white',
               fontsize=7, framealpha=0.9)

    # ── Panel 5: Summary Table ──────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, 1])
    ax5.set_facecolor(BG_PANEL2)
    ax5.axis('off')
    ax5.set_title('Summary Statistics', color='white',
                  fontsize=11, fontweight='bold', pad=8)

    rows = []
    for k, v in results.items():
        if 'avg' not in v:
            continue
        rows.append([
            k.replace('_', ' ')[:24],
            f"{v['min']:.1f}",
            f"{v['avg']:.1f}",
            f"{v['max']:.1f}",
            f"{v.get('jitter', 0):.1f}",
            f"{v.get('loss_pct', 0)}%",
            str(v.get('count', 0)),
        ])

    if rows:
        cols = ['Scenario', 'Min\n(ms)', 'Avg\n(ms)',
                'Max\n(ms)', 'Jitter\n(ms)', 'Loss', 'N']
        tbl = ax5.table(cellText=rows, colLabels=cols,
                        loc='center', cellLoc='center')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7.5)
        tbl.scale(1, 1.55)
        for (row, col), cell in tbl.get_celld().items():
            cell.set_facecolor('#0f3460' if row == 0 else BG_PANEL2)
            cell.set_text_props(color='white')
            cell.set_edgecolor(SPINE_CLR)
            if row == 0:
                cell.set_text_props(color='#00d4ff', fontweight='bold')

    # ── Main title ──────────────────────────────────────────────────
    fig.suptitle(
        'SDN Network Delay Measurement – Analysis Dashboard',
        color='white', fontsize=15, fontweight='bold',
    )

    out = 'delay_analysis.png'
    plt.savefig(out, dpi=150, bbox_inches='tight',
                facecolor=BG_DARK, edgecolor='none')
    print(f'  ✔ Graph saved → {out}')

    # Try to display (works if running with a desktop)
    try:
        matplotlib.use('TkAgg')
        plt.show()
    except Exception:
        pass


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    results = load_results()
    print_summary(results)
    generate_plots(results)
    print('  Done.\n')


if __name__ == '__main__':
    main()
