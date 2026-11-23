"""Real-widget regression coverage for the Quiet Friday IDE redesign.

Run: xvfb-run -a python -m unittest discover -s dev_tests -p test_quiet_workbench_gui.py -v
Uses temporary real files, queued fake hosts, a real Tk event loop,
actual CTk controls, and Tcl bgerror capture. No live model request.
"""
from pathlib import Path
from queue import Queue
from threading import Thread
from types import SimpleNamespace
import inspect
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ide'))
sys.path.insert(0,str(ROOT))  # ide.py also imports repo-root siblings (touchpad_scroll)
import ide
import customtkinter as ctk
from quiet.chat import ChatState


class Host:
    def __init__(self):self.calls=[];self.callbacks=[];self.notify=self.build=None
    def ask(self,text,on_done):self.calls.append(text);self.callbacks.append(on_done)
    def get_circle_rect(self):return None
    def on_ide_closed(self):pass
    def register_ide_chat_notify(self,handler):self.notify=handler
    def register_ide_build_status(self,handler):self.build=handler


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'src').mkdir();(self.root/'src/main.py').write_text("def greet(name):\n    return f'Hello, {name}'\n\nprint(greet('Friday'))\n")
        (self.root/'notes.txt').write_text('a small searchable thought\n')
        self.host=Host();self.errors=[]
        self.app=ide.IDEApp(str(self.root),show_chat=True,agent_host=self.host)
        self.app.geometry('1480x880+15+15')
        self.app.report_callback_exception=lambda *args:self.errors.append(args)
        self.app.tk.createcommand('workbench_test_bgerror',lambda text:self.errors.append(text))
        self.app.tk.eval('proc bgerror {message} {workbench_test_bgerror $message}')
        self.pump(.12)
    def tearDown(self):
        if self.alive(self.app):self.app.destroy()
        self.tmp.cleanup();self.assertEqual(self.errors,[], 'No Python/Tcl GUI callback failures allowed')
    @staticmethod
    def alive(widget):
        try:return bool(widget.winfo_exists())
        except tk.TclError:return False
    def pump(self,seconds=.08):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.app.update();time.sleep(.004)
    def wait(self,predicate,timeout=3):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:self.pump(.02)
        self.assertTrue(predicate(),'UI result did not arrive')
    def chat(self):return self.app.chat_panel

    def test_core_api_is_inherited_and_original_menu_actions_retained(self):
        original=ide._OriginalIDEApp
        for name,method in original.__dict__.items():
            if callable(method) and name not in {'__init__','_build_menubar','_build_body','_build_statusbar','flat_button','tooltip','_dialog','_rebuild_ui','destroy','cmdcenter_set','_refresh_title'}:
                self.assertTrue(hasattr(self.app,name),name)
                self.assertEqual(inspect.signature(getattr(type(self.app),name)),inspect.signature(method),name)
        expected=[section+': '+item[0] for section,items in self.app._quiet_menu_data.items() for item in items if item]
        self.assertTrue(set(expected)<={name for name,command in self.app.COMMANDS})
        self.assertIsInstance(self.app._quiet_header,ctk.CTkFrame)
        self.assertIsInstance(self.app.activity_bar,ctk.CTkFrame)
        self.assertIsInstance(self.chat(),ctk.CTkFrame)
        self.assertIsInstance(self.app.editor.text,tk.Text)

    def test_palette_icons_and_explicit_fonts_match_quiet(self):
        self.assertEqual(ide.C['editor'],'#151517');self.assertEqual(ide.C['sidebar'],'#1b1b1e')
        self.assertEqual(ide.C['accent'],'#c7c3bc')
        self.assertIsInstance(self.app.run_button.cget('font'),tuple)
        bank=self.app._quiet_icons
        images=list(bank.images.values())+list(bank.chat.images.values())
        self.assertTrue(images)
        for image in images:
            self.assertIs(image._master,self.app)
            for photo in image._scaled_dark_photo_images.values():self.assertIs(photo.tk,self.app.tk)

    def test_editor_file_save_highlight_and_dirty_tab_still_work(self):
        self.app.open_file(str(self.root/'src/main.py'));self.pump()
        text=self.app.editor.text;self.app.editor.highlight()
        self.assertTrue(text.tag_ranges('keyword'))
        text.insert('end','\n# another thought\n');self.pump()
        self.assertTrue(self.app.current_buffer()['modified'])
        self.assertTrue(self.app.save_file())
        self.assertIn('another thought',(self.root/'src/main.py').read_text())
        self.assertFalse(self.app.current_buffer()['modified'])
        self.assertIsInstance(self.app.editor.tabbar,ctk.CTkFrame)
        self.assertTrue(self.app.editor.tabs_row.winfo_children())

    def test_activity_mouse_clicks_and_workspace_search(self):
        self.app.activity_bar._buttons['search'].event_generate('<Button-1>',x=5,y=5);self.pump()
        self.assertEqual(self.app.sidebar.mode,'search')
        self.app.sidebar.s_query.insert(0,'searchable');self.app.sidebar._run_search();self.pump()
        self.assertEqual(len(self.app.sidebar._search_hits),1)
        self.assertEqual(self.app.sidebar._search_hits[0][1],1)
        self.app.activity_bar._buttons['explorer'].event_generate('<Button-1>',x=5,y=5);self.pump()
        self.assertEqual(self.app.sidebar.mode,'explorer')

    def test_multiline_entry_compatibility_placeholder_and_send(self):
        panel=self.chat();self.assertIsInstance(panel.entry,ctk.CTkTextbox)
        self.assertEqual(panel.entry.get(),'');self.assertEqual(panel.send_button.cget('state'),'disabled')
        panel._clear_placeholder();panel.entry.insert(0,'first line');panel.entry.focus_force();self.pump()
        panel.entry.event_generate('<Shift-Return>');self.pump()
        self.assertIn('\n',panel.entry.get());self.assertEqual(self.host.calls,[])
        panel.entry.event_generate('<Return>');self.pump()
        self.assertEqual(self.host.calls,['first line']);self.assertEqual(panel.entry.get(),'')
        self.assertIsNotNone(panel.state.pending_id)
        self.host.callbacks[-1](True,'## A clear answer.\n\nA little room to think.')
        self.wait(lambda:panel.state.pending_id is None)
        self.assertIn('A clear answer',panel.state.messages[-1]['text'])

    def test_duplicate_send_guard_and_foreign_thread_reply(self):
        panel=self.chat();panel.use_prompt('one request');panel._send();panel.use_prompt('a kept draft');panel._send()
        self.assertEqual(self.host.calls,['one request']);self.assertEqual(panel.entry.get(),'a kept draft')
        callback=self.host.callbacks[-1]
        thread=Thread(target=lambda:callback(True,'Result on the GUI queue'))
        thread.start();thread.join();self.wait(lambda:panel.state.pending_id is None)
        self.assertEqual(panel.state.messages[-1]['text'],'Result on the GUI queue')

    def test_theme_preserves_chat_draft_transcript_and_pending_result(self):
        panel=self.chat();panel.use_prompt('a request');panel._send();panel.use_prompt('unsent next thought')
        state=panel.state;callback=self.host.callbacks[-1]
        self.app.set_theme('black');self.pump(.15)
        new=self.chat();self.assertIsNot(new,panel);self.assertIs(new.state,state)
        self.assertEqual(new.entry.get(),'unsent next thought');self.assertIsNotNone(state.pending_id)
        callback(True,'A reply after the theme switch',['Continue','Revise'])
        self.wait(lambda:state.pending_id is None)
        self.assertEqual(state.messages[-1]['text'],'A reply after the theme switch')
        self.assertEqual(state.options,['Continue','Revise']);self.assertIsNotNone(new._decision_row)
        self.app.set_theme('grey');self.pump(.1)
        self.assertEqual(self.chat().entry.get(),'unsent next thought')
        self.assertEqual(self.chat().state.options,['Continue','Revise'])

    def test_theme_preserves_unsaved_buffer_content_flags_and_position(self):
        self.app.open_file(str(self.root/'notes.txt'));self.pump()
        text=self.app.editor.text;text.insert('end','UNSAVED');self.pump();text.mark_set('insert','1.5')
        self.app.set_theme('white');self.pump(.15)
        self.assertIn('UNSAVED',self.app.editor.text.get('1.0','end'))
        self.assertTrue(self.app.current_buffer()['modified'])
        self.assertEqual(self.app.editor.text.index('insert'),'1.5')
        self.app.current_buffer()['modified']=False
        self.app.set_theme('grey');self.pump(.1)

    def test_inline_permission_buttons_and_custom_answers_keep_original_contract(self):
        panel=self.chat();panel._show_decision_options(['Keep it small','Add a layer']);self.pump()
        self.assertIsNotNone(panel._decision_row)
        buttons=[w for w in panel._decision_row.winfo_children() if isinstance(w,ctk.CTkButton)]
        next(b for b in buttons if b.cget('text')=='Keep it small').invoke()
        self.assertEqual(self.host.calls,['Keep it small']);self.assertIsNone(panel._decision_row)
        self.host.callbacks[-1](True,'Which name?',['Short','Long']);self.wait(lambda:panel.state.pending_id is None)
        custom=[w for frame in panel._decision_row.winfo_children() for w in frame.winfo_children() if isinstance(w,ctk.CTkEntry)][0]
        custom.insert(0,'My own answer');custom.focus_force();self.pump();custom._entry.event_generate('<Return>');self.pump()
        self.assertEqual(self.host.calls[-1],'My own answer');self.assertIsNone(panel._decision_row)

    def test_build_phase_upsert_and_proactive_messages_survive_rebuild(self):
        panel=self.chat();self.host.build('frontend','in_progress','Layout and controls');self.pump(.1)
        row=panel._status_row;self.assertIsNotNone(row)
        self.host.build('frontend','done','Ready to review');self.pump(.1)
        self.assertIs(panel._status_row,row)
        self.assertIn('Ready to review',panel._status_labels['frontend'].cget('text'))
        self.host.notify('A new thought from Friday',['Proceed','Hold']);self.pump(.1)
        self.assertEqual(panel.state.messages[-1]['text'],'A new thought from Friday')
        self.assertIsNotNone(panel._decision_row)
        self.app.set_theme('black');self.pump(.15)
        self.assertIn('Ready to review',self.chat()._status_labels['frontend'].cget('text'))

    def test_context_attachment_is_workspace_reference_not_a_file_upload(self):
        panel=self.chat();path=self.root/'src/main.py'
        with patch('quiet.chat.filedialog.askopenfilenames',return_value=[str(path)]), \
             patch.object(Path,'read_bytes',side_effect=AssertionError('Do not read uploads')):
            panel.attach_files()
        self.assertEqual(panel.state.references,['src/main.py'])
        panel.use_prompt('Review this');panel._send()
        self.assertIn('src/main.py',self.host.calls[-1]);self.assertIn('not uploads',self.host.calls[-1])
        with patch('quiet.chat.filedialog.askopenfilenames',return_value=['/outside/project.txt']):panel.attach_files()
        self.assertEqual(panel.state.references,[])

    def test_unavailable_voice_controls_and_copy_export_are_honest(self):
        panel=self.chat();panel.mic_button.invoke();self.assertIn("isn't ready on this machine",panel._last_notice)
        # the chat speaker control was removed by product decision (TTS is
        # always-on via the circle); what must remain true is that no fake
        # speaker control invites clicks that would do nothing
        self.assertFalse(hasattr(panel,'speaker_button'))
        panel.copy_message('A copied answer');self.assertEqual(self.app.clipboard_get(),'A copied answer')
        output=self.root/'chat.txt'
        with patch('quiet.chat.filedialog.asksaveasfilename',return_value=str(output)):panel.export_transcript()
        self.assertIn('Friday',output.read_text())

    def test_run_file_and_terminal_history_survive_theme_change(self):
        self.app.open_file(str(self.root/'src/main.py'));self.pump();self.app.run_file()
        self.wait(lambda:'Hello, Friday' in self.app.get_terminal_output())
        self.wait(lambda:self.app.task_proc is None)
        self.app.panel.history.append('a previous command')
        self.app.set_theme('black');self.pump(.15)
        self.assertIn('Hello, Friday',self.app.get_terminal_output())
        self.assertIn('a previous command',self.app.panel.history)
        self.assertIsInstance(self.app.panel.header,ctk.CTkFrame)

    def test_find_replace_has_original_entry_api(self):
        self.app.open_file(str(self.root/'notes.txt'));self.pump();self.app.show_find(True)
        ed=self.app.editor;ed.fe.insert(0,'small');ed.do_find();self.assertEqual(len(ed._matches),1)
        ed.re.insert(0,'clear');ed.replace_all();self.pump()
        self.assertIn('a clear',ed.text.get('1.0','end'))
        ed.fe.selection_range(0,'end')

    def test_sidebar_chat_and_terminal_visibility_remain_user_controlled(self):
        panel=self.chat();self.app.toggle_chat();self.assertFalse(self.app.chat_visible)
        self.app.toggle_chat();self.assertTrue(self.app.chat_visible);self.assertIs(self.chat(),panel)
        self.app.toggle_sidebar();self.pump(.2);self.assertFalse(self.app.sidebar_visible)
        self.app.toggle_sidebar();self.pump(.2);self.assertTrue(self.app.sidebar_visible)
        self.app.toggle_panel();self.assertTrue(self.app.panel_visible)
        self.app.toggle_panel();self.assertFalse(self.app.panel_visible)

    def test_small_and_wide_layout_keep_composer_run_and_code_visible(self):
        self.app.open_file(str(self.root/'src/main.py'));self.pump()
        for width,height in [(900,560),(1200,750),(1480,880)]:
            self.app.geometry(f'{width}x{height}+15+15');self.pump(.15)
            panel=self.chat()
            for widget in [panel.entry,panel.send_button,self.app.run_button,self.app.editor.text]:
                self.assertTrue(widget.winfo_ismapped(),str(widget))
                self.assertGreater(widget.winfo_width(),0)
            self.assertLessEqual(panel.send_button.winfo_rootx()+panel.send_button.winfo_width(),self.app.winfo_rootx()+self.app.winfo_width())
            self.assertLessEqual(self.app.run_button.winfo_rootx()+self.app.run_button.winfo_width(),self.app.winfo_rootx()+self.app.winfo_width())

    def test_many_tabs_are_scrollable_and_every_file_remains_reachable(self):
        for index in range(12):
            path=self.root/f'a_fairly_long_filename_{index}.py';path.write_text(f'x={index}\n')
            self.app.open_file(str(path))
        self.pump(.1)
        self.assertEqual(len(self.app.buffers),12)
        self.assertEqual(len(self.app.editor.tabs_row.winfo_children()),12)
        self.assertTrue(self.app.editor.tabs_row._parent_canvas.xview()[1]<1)
        self.app.switch_tab(0);self.assertEqual(self.app.current_buffer()['name'],'a_fairly_long_filename_0.py')
        self.app.switch_tab(11);self.assertEqual(self.app.current_buffer()['name'],'a_fairly_long_filename_11.py')

    def test_widget_scaling_keeps_composer_and_editor_controls_visible(self):
        self.app.open_file(str(self.root/'src/main.py'));self.pump()
        try:
            ctk.set_widget_scaling(1.25);self.pump(.15)
            panel=self.chat();panel._add_bubble('friday','An answer with a fairly long paragraph. '*20)
            self.pump(.1)
            self.assertTrue(panel.entry.winfo_ismapped())
            self.assertLessEqual(panel.send_button.winfo_rootx()+panel.send_button.winfo_width(),
                                 self.app.winfo_rootx()+self.app.winfo_width())
            right=panel.log._parent_canvas.winfo_rootx()+panel.log._parent_canvas.winfo_width()
            for label,role in panel._wrap_labels:
                self.assertLessEqual(label.winfo_rootx()+label.winfo_width(),right)
        finally:ctk.set_widget_scaling(1.0);self.pump(.05)

    def test_running_task_output_is_not_lost_when_theme_changes(self):
        path=self.root/'stream.py'
        path.write_text("import time\nprint('before-change',flush=True)\ntime.sleep(.4)\nprint('after-change',flush=True)\n")
        self.app.open_file(str(path));self.pump();self.app.run_file()
        self.wait(lambda:'before-change' in self.app.get_terminal_output())
        self.app.set_theme('black');self.pump(.1)
        self.wait(lambda:'after-change' in self.app.get_terminal_output())
        self.wait(lambda:self.app.task_proc is None)
        self.assertIn('before-change',self.app.get_terminal_output())

    def test_actual_titlebar_mouse_close_does_not_leave_callbacks(self):
        buttons=[w for w in self.app._winctrl_zone.winfo_children() if isinstance(w,ctk.CTkButton)]
        self.assertTrue(buttons)
        close=buttons[-1]
        close.event_generate('<Button-1>',x=5,y=5)
        self.assertFalse(self.alive(self.app))
        self.assertEqual(close._owned_after_jobs,set())

    def test_shutdown_cancels_widget_callbacks_images_and_pending_reply(self):
        panel=self.chat();panel.use_prompt('close during this request');panel._send()
        callback=self.host.callbacks[-1];state=panel.state;bank=self.app._quiet_icons
        self.app.destroy();callback(True,'No widget should be retained by this callback')
        self.assertFalse(self.alive(self.app));self.assertEqual(bank.images,{})
        self.assertTrue(panel._closed);self.assertEqual(panel._owned_after_jobs,set())
        self.assertEqual(bank.chat.images,{})
        self.assertEqual(state.results.get_nowait()[2],'No widget should be retained by this callback')


if __name__=='__main__':unittest.main(verbosity=2)
