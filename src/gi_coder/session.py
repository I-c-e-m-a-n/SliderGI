"""macOS terminal signals unwind the CLI so its runtime finally blocks run."""
from contextlib import contextmanager
import signal


class SessionTerminated(BaseException):
    def __init__(self, signum):
        self.signum = signum


@contextmanager
def session_signals():
    previous = {}
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        # A second terminal signal must not interrupt child-process cleanup.
        if stopping:
            return
        stopping = True
        raise SessionTerminated(signum)
    try:
        for name in ('SIGHUP', 'SIGTERM', 'SIGINT'):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous[signum] = signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
