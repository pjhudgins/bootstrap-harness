"""safe_probe.py: a harmless script for testing the harness's Python execute tool.

It only reports facts about how it was run. It opens no files and no network,
starts no processes, and changes nothing. Any arguments are echoed back.

Promoted to scripts/ by a human (task 5 set-up, 2026-09-25). Agents cannot write here.
"""

import os
import platform
import sys
import time

print("safe_probe: running")
print(f"python      {platform.python_version()} ({sys.executable})")
print(f"isolated    {bool(sys.flags.isolated)}  (expected True: run with -I)")
print(f"utf8 mode   {bool(sys.flags.utf8_mode)}")
print(f"cwd         {os.getcwd()}")
print(f"args        {sys.argv[1:]}")
print(f"env keys    {sorted(os.environ)}")
print(f"utc time    {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
print(f"arithmetic  2**10 = {2 ** 10}")
print("unicode     ✓ é 中")
print("safe_probe: done", file=sys.stderr)
