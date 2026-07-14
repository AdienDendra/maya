from contextlib import contextmanager
import maya.cmds as cmds


@contextmanager
def undo_chunk(name="AD Skin Tool Operation"):
    """
    Wrap Maya operations in one undo step.

    This makes the tool feel native:
    user applies one skin operation, then Ctrl+Z undoes that operation in one step.
    """
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)