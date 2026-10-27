"""Concise console output for maintained AI-PAL workflows."""

import os
import sys
import time
import math
from threading import Lock


LEVELS = {"quiet": 0, "default": 1, "debug": 2}
_ORIGINAL_STDOUT = sys.stdout
_ACTIVE_STREAM = None
_VERBOSITY = 'default'


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
        "stale repick status ignored ",
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
        # Per-association search/cache diagnostics are debug output, not progress.
        if stripped.startswith("[assoc]"):
            return False
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
    global _ACTIVE_STREAM, _VERBOSITY
    configured = verbosity
    if configured is None and cfg is not None:
        configured = getattr(cfg, "console_verbosity", None)
    configured = os.environ.get("AI_PAL_CONSOLE_VERBOSITY", configured)
    configured = normalize_verbosity(configured)
    _VERBOSITY = configured
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


def debug(message):
    if os.environ.get('AI_PAL_CONSOLE_VERBOSITY', _VERBOSITY) == 'debug':
        print(message, flush=True)


def warning(message, flush=True):
    print("[WARN] {}".format(message), flush=flush)


class Progress:
    """Report stage start, throttled completed units, and stage completion."""
    def __init__(self, label, total, interval_sec=60.0, enabled=True):
        if isinstance(interval_sec, bool) or not math.isfinite(interval_sec) or interval_sec <= 0:
            raise ValueError('progress_interval_sec must be finite and positive')
        self.label, self.total, self.interval = label, total, interval_sec
        self.enabled = enabled
        self.started = self.last_report = time.monotonic()
        self.finished = total == 0
        if enabled:
            log('progress', '{} 0/{} | elapsed 0s'.format(label, total))

    def update(self, completed):
        if not self.enabled or self.finished:
            return
        now = time.monotonic()
        if completed >= self.total or now - self.last_report >= self.interval:
            log('progress', '{} {}/{} | elapsed {:.0f}s'.format(
                self.label, completed, self.total, now - self.started))
            self.last_report = now
            self.finished = completed >= self.total
