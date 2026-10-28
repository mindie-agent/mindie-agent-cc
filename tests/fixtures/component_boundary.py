"""Emit real process output before a caller-imposed deadline or output limit."""
import os
import sys
import threading

print(f"component began pid={os.getpid()}", flush=True)
print("component warning", file=sys.stderr, flush=True)
if sys.argv[1] == "wait":
    threading.Event().wait(30)
else:
    print("x" * 8192, flush=True)
