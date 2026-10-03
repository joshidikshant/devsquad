"""Internal deterministic lifecycle fixture. It is not a public task command."""
import argparse
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--delay", type=float, default=0.05)
delay = parser.parse_args().delay
time.sleep(delay)
sys.stdout.write("M2_FAKE_STEP_OK\n")
