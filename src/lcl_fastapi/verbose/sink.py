"""Adapt diagnostic admission while retaining native file ownership and rotation."""

import copy
import logging

from lclang.logger.file_sink import FileSink


class DiagnosticFileSink(FileSink):
    """Borrow an existing native sink without opening another stream or segment.

    :param original: Native sink retaining all file resources, counters and timers.
    """

    def __init__(self, original: FileSink) -> None:
        """Retain the original owner; dispatcher cleanup closes it exactly once.

        :param original: Already initialized native file sink.
        """
        self.original = original
        self.key, self.config, self.formatter = original.key, original.config, original.formatter

    def accepts(self, record: logging.LogRecord) -> bool:
        """Keep source filters and normal thresholds, admitting pinned diagnostics at DEBUG.

        :param record: Queue-owned record with a producer-captured file policy.
        :returns: Whether this existing file receives the intact record.
        """
        if hasattr(record, "lcl_file_verbose"):
            if not record.lcl_file_verbose:
                return False
            probe = copy.copy(record)
            probe.levelno = max(record.levelno, self.config.level)
            return self.original.accepts(probe)
        return self.original.accepts(record)

    def write(self, record: logging.LogRecord) -> None:
        """Delegate intact record formatting and rotation to the original owner.

        :param record: Admitted original record, with unchanged numeric severity.
        :raises OSError: If native writing or rotation fails.
        """
        self.original.write(record)

    def process_timers(self, monotonic: float, wall: float) -> None:
        """Delegate native rollover and flush scheduling.

        :param monotonic: Current monotonic seconds.
        :param wall: Current Unix seconds.
        :raises OSError: If native timer I/O fails.
        """
        self.original.process_timers(monotonic, wall)

    def timeout(self, monotonic: float, wall: float) -> float:
        """Retain the native timer wake-up deadline.

        :param monotonic: Current monotonic seconds.
        :param wall: Current Unix seconds.
        :returns: Seconds until the next native writer wake-up.
        """
        return self.original.timeout(monotonic, wall)

    def close(self) -> None:
        """Close the original native sink, retaining its flush and failure policy.

        :raises OSError: If native flush or close fails.
        """
        self.original.close()
