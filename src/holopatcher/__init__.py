"""HoloPatcher application identity; importing the package has no startup effects."""
from enum import IntEnum

CURRENT_VERSION = "2.0b"


class ExitCode(IntEnum):
    SUCCESS = 0
    NUMBER_OF_ARGS = 2
    NAMESPACE_INDEX_OUT_OF_RANGE = 4
    ABORT_INSTALL_UNSAFE = 6
    EXCEPTION_DURING_INSTALL = 7
    INSTALL_COMPLETED_WITH_ERRORS = 8
    INTERRUPTED = 130
