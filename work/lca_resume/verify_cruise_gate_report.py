#!/usr/bin/env python3
"""Verify the cruise-gate report against a fresh read-only analysis run."""
from __future__ import annotations
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ANALYZER = os.path.join(HERE, "analyze_cruise_gate.py")
REPORT = os.path.join(HERE, "cruise_gate_report.md")

p = subprocess.run([sys.executable, ANALYZER], cwd=ROOT, text=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
checks = {
    "analyzer exits zero": p.returncode == 0,
    "CV config lacks 0x1A0": "CV6T-AB: RX IDs (16)" in p.stdout and
                              p.stdout.count("0x1A0 descriptor: ABSENT") == 2,
    "no configured named cruise signal":
        p.stdout.count("named Cc/ACC signals in configured RX frames: NONE") == 2,
    "no exact CcStat extraction":
        p.stdout.count("extraction records with exact CcStat spec 0E01: 0") == 2,
    "getter positive control": "getter-pointer X:$408A callers: P:$1C7A7" in p.stdout,
    "AH LCA dispatcher": "dispatcher P:$28CEB, lane X:$28D6" in p.stdout,
    "BV LCA dispatcher": "dispatcher P:$26911, lane X:$23E4" in p.stdout,
    "AH torque enable": "torque enable P:$295BE, code X:$28B1" in p.stdout,
    "BV torque enable": "torque enable P:$274AE, code X:$23BF" in p.stdout,
    "four AH state-4 consumers":
        "lane==4 sites: P:$29170 P:$291EF P:$29227 P:$29263" in p.stdout,
    "four BV state-4 consumers":
        "lane==4 sites: P:$26D5A P:$26DD9 P:$26E11 P:$26E4D" in p.stdout,
    "torque functions differ only at 14 operands":
        "107-word functions P:$29586/P:$27476: differing words=14" in p.stdout,
    "report exists": os.path.isfile(REPORT),
}
if os.path.isfile(REPORT):
    md = open(REPORT, encoding="utf-8").read()
    checks.update({
        "report records strict verdict": "Прямая зависимость LCA" in md and "опровергнута" in md,
        "report preserves limitation": "не исключено" in md,
        "report cites raw AH dispatcher": "P:$28CEB" in md and "F07C 28C3" in md,
        "report cites raw BV control": "P:$26911" in md and "F07C 23D1" in md,
    })

ok = True
for name, passed in checks.items():
    print(f"{'PASS' if passed else 'FAIL'}  {name}")
    ok &= passed
if p.stderr:
    print("analyzer stderr:", p.stderr, file=sys.stderr)
print("VERIFY:", "ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
