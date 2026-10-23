"""Drop the current process to low OS priority so long jobs don't hog the machine.

Child processes (multiprocessing workers, subprocess sims) inherit the class on
Windows; on POSIX we use nice(). Call set_low_priority() at the top of main().
"""
import os
import sys


def set_low_priority(idle=False):
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL = 0x00004000
            IDLE = 0x00000040
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), IDLE if idle else BELOW_NORMAL)
        else:
            os.nice(19 if idle else 10)
    except Exception:
        pass
