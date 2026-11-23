"""Actual app-panel-first / IDE-second native interpreter integration.

Run: xvfb-run -a python dev_tests/test_quiet_interpreter_ownership.py
No Qt/provider/voice service is invoked. Real manager/agent bridge APIs and
real native widgets are used; this tests the wrong-default-root failure.
"""
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import agent_tools as tools
import memory
import customtkinter as ctk


def main():
    with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as store:
        memory.set_memory_dir(store)
        root=Path(folder);(root/'module.py').write_text('print("a clear thought")\n')
        # The native app-panel host becomes tkinter's global default
        # interpreter before the IDE's GUI owner exists at all.
        manager=tools._panel_manager
        manager._ensure_host()
        try:
            assert 'Agent Mode is on' in tools.agent_mode_on(str(root))
            assert 'Opened' in tools.ide_open_file('module.py')
            def inspect():
                app=tools._ide_app
                assert app.tk is not manager._root.tk
                assert app.sidebar._icon('module.py').tk is app.tk
                for bank in [app._quiet_icons.chat,app._quiet_icons]:
                    for image in bank.images.values():
                        for photo in image._scaled_dark_photo_images.values():assert photo.tk is app.tk
                assert isinstance(app.chat_panel.entry,ctk.CTkTextbox)
                assert 'a clear thought' in app.editor.text.get('1.0','end')
                return True
            assert tools._run_on_main(inspect)
            # Native work panels can still render beside the separate
            # IDE interpreter and their unchanged registry contract.
            assert 'Opened Chat' in manager.open('chat')
            assert manager._bridge.call(lambda:manager._panels['chat']['frame'].chat_view.tk is manager._root.tk)
            assert 'Closed Chat' in manager.close('chat')
            assert 'a clear thought' in tools.ide_get_active_tab_content()
            assert len(tools.IDE_CHAT_TOOL_NAMES)==64
            assert len(tools.TOOL_FUNCTIONS)==104
            assert 'Agent Mode is off' in tools.agent_mode_off()
        finally:
            tools.agent_mode_off();manager.shutdown();memory.set_memory_dir(None)
    print('PASS — app-panel-first/IDE-second images, editor, Chat panel and 64/104 tool parity')


if __name__=='__main__':main()
