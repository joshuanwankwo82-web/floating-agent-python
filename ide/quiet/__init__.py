"""Quiet-inspired CustomTkinter presentation on Friday's original IDE core.

Only presentation classes are substituted. Every inherited editor, file,
runner, media and agent API remains available to the unchanged tool layer.
"""
from .tokens import install_palette
from .panels import make_panels
from .editor import make_editor
from .chat import make_chat
from .workspace import make_workspace


def install(core):
    install_palette(core)
    core._OriginalEditorArea=core.EditorArea
    activity,sidebar,panel,status=make_panels(core)
    editor=make_editor(core)
    chat=make_chat(core)
    classes=(activity,sidebar,panel,status,editor,chat)
    mixin=make_workspace(core,classes)
    original=core.IDEApp
    class IDEApp(mixin,original):
        """Friday's original IDE functionality with the Quiet CTk workbench."""
        pass
    IDEApp.__module__=core.__name__
    core._OriginalIDEApp=original
    core._OriginalEditorArea=core.EditorArea
    core._OriginalChatSidePanel=core.ChatSidePanel
    core.ActivityBar,core.SideBar,core.Panel,core.StatusBar,core.EditorArea,core.ChatSidePanel=classes
    core.IDEApp=IDEApp
