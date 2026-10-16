"""Local evidence viewer. Does not execute agents or alter rewards."""
__version__ = "0.10.0"
from .sdk import Recorder

# Delay the optional method evidence import to avoid SDK/store import cycles.
def __getattr__(name):
    if name == "StudioRecorder":
        from .studio.sdk import StudioRecorder
        return StudioRecorder
    if name == "MethodRecorder":
        from .methods.sdk import MethodRecorder
        return MethodRecorder
    raise AttributeError(name)

__all__ = ["Recorder", "MethodRecorder", "StudioRecorder", "__version__"]
