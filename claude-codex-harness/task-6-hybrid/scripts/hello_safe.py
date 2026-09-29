"""A safe test script for the task-6 harness's python_exec tool.

It reads no file, writes no file, opens no network connection and starts no process.
It prints a greeting, the Python version, its arguments and a small computation, so an
agent can check that execution, argument passing and output capture work. With the
argument --fail it writes one line to stderr and exits 2, to show both of those too.
"""

import platform
import sys


def is_number(text):
    try:
        float(text)
    except ValueError:
        return False
    return True


def main(argv):
    print("hello from the NIMOI task-6 scripts folder")
    print(f"python {platform.python_version()}")
    print(f"arguments: {argv}")
    numbers = [float(a) for a in argv if is_number(a)]
    if numbers:
        print(f"sum of numeric arguments: {sum(numbers)}")
    print(f"squares 1..5: {[n * n for n in range(1, 6)]}")
    if "--fail" in argv:
        print("failing on purpose (--fail)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
