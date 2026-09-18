"""Concise console output for maintained AI-PAL workflows."""

import os
import sys
from threading import Lock


LEVELS = {"quiet": 0, "default": 1, "debug": 2}
_ORIGINAL_STDOUT = sys.stdout
_ACTIVE_STREAM = None


def normalize_verbosity(value):
    value = str(value or "default").strip().lower()
    if value not in LEVELS:
        raise ValueError(
            "console_verbosity must be one of: quiet, default, debug"
        )
    return value


def _is_warning(line):
    lowered = line.lstrip().lower()
    return lowered.startswith((
        "warning:", "error:", "[warn]", "[error]", "bad ", "false ",
        "pipeline error ", "skip repick waveform ",
        "input data too short", "filter type not supported",
        "no event waveform files ", "stale repick status ignored ",
    ))


class _FilteredConsole(object):
    def __init__(self, stream, verbosity):
        self.stream = stream
        self.verbosity = normalize_verbosity(verbosity)
        self.buffer = ""
        self.lock = Lock()

    def _show(self, line):
        if self.verbosity == "debug":
            return True
        if _is_warning(line):
            return True
        stripped = line.lstrip()
        if self.verbosity == "quiet":
            return stripped.startswith(("[output]", "[complete]"))
        return stripped.startswith("[")

    def _emit(self, line, ending):
        if self._show(line):
            lowered = line.lstrip().lower()
            if self.verbosity != "debug" and _is_warning(line) and not (
                lowered.startswith("[warn]") or lowered.startswith("[error]")
            ):
                label = "ERROR" if lowered.startswith((
                    "error:", "pipeline error "
                )) else "WARN"
                line = "[{}] {}".format(label, line)
            self.stream.write(line + ending)

    def write(self, value):
        if not value:
            return 0
        with self.lock:
            self.buffer += str(value)
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                self._emit(line, "\n")
        return len(value)

    def flush(self):
        with self.lock:
            if self.buffer:
                self._emit(self.buffer, "")
                self.buffer = ""
            self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def configure(cfg=None, verbosity=None):
    """Install the output filter and return the resolved verbosity."""
    global _ACTIVE_STREAM
    configured = verbosity
    if configured is None and cfg is not None:
        configured = getattr(cfg, "console_verbosity", None)
    configured = os.environ.get("AI_PAL_CONSOLE_VERBOSITY", configured)
    configured = normalize_verbosity(configured)
    if configured == "debug":
        sys.stdout = _ORIGINAL_STDOUT
        _ACTIVE_STREAM = None
    elif isinstance(sys.stdout, _FilteredConsole):
        sys.stdout.verbosity = configured
        _ACTIVE_STREAM = sys.stdout
    else:
        _ACTIVE_STREAM = _FilteredConsole(_ORIGINAL_STDOUT, configured)
        sys.stdout = _ACTIVE_STREAM
    return configured


def log(tag, message, flush=True):
    print("[{}] {}".format(tag, message), flush=flush)


def warning(message, flush=True):
    print("[WARN] {}".format(message), flush=flush)
