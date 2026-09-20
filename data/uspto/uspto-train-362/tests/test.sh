#!/bin/bash
# Never use set -e: reward.txt must exist on every path.
mkdir -p /logs/verifier
python3 /tests/grader.py
[ -f /logs/verifier/reward.txt ] || echo 0 > /logs/verifier/reward.txt
exit 0
