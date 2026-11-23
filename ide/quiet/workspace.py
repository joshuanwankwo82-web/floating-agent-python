"""The redesigned CTk workspace shell around Friday's original IDEApp."""
from pathlib import Path
import os
import subprocess
import threading
import time
from queue import Queue, Empty
import tkinter as tk
import customtkinter as ctk
from .tokens import ui_font
from .icons import IconBank
from .widgets import _OwnedTimers, Label, Button, PanelToplevel, Tooltip, icon_button, action_button
from .menu_data import menu_data


def make_workspace(core,classes):
    BaseApp=core.IDEApp
    ActivityBar,SideBar,Panel,StatusBar,EditorArea,ChatSidePanel=classes
    class WorkspaceMixin(_OwnedTimers):
        def __init__(self,start_dir=None,agent_controlled=False,agent_host=None,
                     initial_bounds=None,show_chat=False):
            core.C.clear();core.C.update(core.THEMES[core.DEFAULT_THEME])
            core.TAG_COLOR.clear();core.TAG_COLOR.update(core._tag_colors());core._refresh_guide_color()
            self._quiet_owner_thread=threading.get_ident()
            self._quiet_cross_thread=Queue();self._quiet_cancelled_jobs=set();self._quiet_post_counter=0
            self._quiet_rebuild_generation=0;self._quiet_restore_job=None
            self._quiet_core=core;self._quiet_show_chat=bool(show_chat)
            self._quiet_icons=None;self._quiet_tooltips=[];self._quiet_rebuilding=False
            self._quiet_closing=False;self._quiet_menus=[];self._quiet_header_job=None
            super().__init__(start_dir=start_dir,agent_controlled=agent_controlled,
                             agent_host=agent_host,initial_bounds=initial_bounds)
            self._restore_native_root_configure()
            self._quiet_last_post=0.0
            self._quiet_post_job=self.after(12,self._drain_posts)
            self.title('Friday IDE · '+self.workspace_display_name())
        def after(self,ms,func=None,*args):
            if func is not None and threading.get_ident()!=self._quiet_owner_thread:
                self._quiet_post_counter+=1
                identifier='quiet-post:'+str(self._quiet_post_counter)
                if not self._quiet_closing:
                    self._quiet_cross_thread.put((identifier,ms,func,args))
                return identifier
            return super().after(ms,func,*args)
        def after_cancel(self,identifier):
            if isinstance(identifier,str) and identifier.startswith('quiet-post:'):
                self._quiet_cancelled_jobs.add(identifier);return
            return super().after_cancel(identifier)
        def _drain_posts(self):
            if self._quiet_closing:return
            handled=False
            while True:
                try:identifier,ms,func,args=self._quiet_cross_thread.get_nowait()
                except Empty:break
                handled=True
                if identifier in self._quiet_cancelled_jobs:
                    self._quiet_cancelled_jobs.discard(identifier);continue
                if ms in (0,'idle'):func(*args)
                else:super().after(ms,func,*args)
            if handled:self._quiet_last_post=time.monotonic()
            # Consecutive bridge calls (including read-after-action) must
            # not wait behind a cosmetic typewriter tick. Back off only
            # when the queue is idle, keeping passive CPU work small.
            delay=1 if time.monotonic()-self._quiet_last_post<.1 else 12
            self._quiet_post_job=super().after(delay,self._drain_posts)
        def _restore_native_root_configure(self):
            # CTk 5.x wraps configure on a plain Tk master to propagate
            # background changes. Those closures otherwise retain dead
            # children after a complete theme rebuild. Every CTk surface
            # here has explicit colors; keep native window configuration
            # native, with the geometry/outline contract Friday expects.
            self.configure=tk.Tk.configure.__get__(self,tk.Tk)
            self.config=self.configure
        def tooltip(self,widget,text):
            self._quiet_tooltips.append(Tooltip(widget,text,self))
        def flat_button(self,parent,text,cmd,secondary=False):
            return action_button(self,parent,text,cmd,primary=not secondary)
        def _build_menubar(self):
            c=core.C
            if self._quiet_icons is None:self._quiet_icons=IconBank(self)
            bar=ctk.CTkFrame(self,fg_color=c['menubar'],height=64,corner_radius=0)
            bar.pack(fill='x',side='top',padx=12,pady=(8,4));bar.pack_propagate(False)
            self._quiet_header=bar
            bar.grid_columnconfigure(2,weight=1)
            brand=ctk.CTkFrame(bar,fg_color='transparent')
            brand.grid(row=0,column=0,sticky='w',padx=(10,16),pady=10)
            mark=Label(brand,text='✧' if self._quiet_icons('spark',c['accent'],22) is None else '',
                       image=self._quiet_icons('spark',c['accent'],22),font=ui_font(self,16),width=30,text_color=c['accent'])
            mark.pack(side='left',padx=(0,7))
            Label(brand,text='friday',font=ui_font(self,21,'bold'),text_color=c['fg']).pack(side='left')
            Label(brand,text='IDE',font=ui_font(self,10),text_color=c['fg_faint']).pack(side='left',padx=(8,0),pady=(4,0))
            menurow=ctk.CTkFrame(bar,fg_color='transparent');menurow.grid(row=0,column=1,sticky='w')
            data=menu_data(self);self._quiet_menus=[];self._quiet_menu_buttons={}
            self._menu_commands=[(section+': '+item[0],item[2]) for section,items in data.items() for item in items if item]
            self._menu_commands.append(('View: Toggle Friday chat',self.toggle_chat))
            self.COMMANDS=self._menu_commands
            self._quiet_menu_data=data
            for section in ['File','Edit','View','Run']:
                menu=self._menu(data[section]);self._quiet_menus.append(menu)
                button=Button(menurow,text=section,font=ui_font(self,12),width=43,height=30,
                              fg_color='transparent',hover_color=c['hover'],text_color=c['fg_dim'],corner_radius=8)
                button.configure(command=lambda b=button,m=menu:self._popup(m,b));button.pack(side='left',padx=1)
                self._quiet_menu_buttons[section]=button
            more=tk.Menu(self,tearoff=0,bg=c['sidebar'],fg=c['fg'],activebackground=c['list_sel'],
                         activeforeground=c['fg'],bd=0,font=self.f_ui)
            for section,items in data.items():
                more.add_cascade(label=section,menu=self._menu(items))
            more.add_separator();more.add_command(label='Toggle Friday chat',command=self.toggle_chat)
            self._quiet_menus.append(more)
            morebutton=icon_button(self,menurow,'more',lambda:self._popup(more,morebutton),'All menus and commands',size=16,width=28,height=30)
            morebutton.pack(side='left',padx=(2,0));self._quiet_more=morebutton
            center=ctk.CTkFrame(bar,fg_color='transparent')
            center.grid(row=0,column=2,sticky='ew',padx=(18,18))
            self.cmdcenter=ctk.CTkEntry(center,width=250,height=34,font=ui_font(self,11),
                                       fg_color=c['input'],text_color=c['fg_dim'],border_color=c['border'],
                                       border_width=1,corner_radius=10)
            self.cmdcenter.pack(fill='x');self.cmdcenter.configure(state='readonly')
            self.cmdcenter.bind('<Button-1>',lambda e:self.quick_open(''))
            self.tooltip(self.cmdcenter,'Go to file or command · Ctrl+P / Ctrl+Shift+P')
            actions=ctk.CTkFrame(bar,fg_color='transparent');actions.grid(row=0,column=3,sticky='e',padx=(0,8))
            self.run_button=Button(actions,text='Run',image=self._quiet_icons('play',c['fg'],13),
                                   command=self.run_file,font=ui_font(self,12),width=70,height=32,corner_radius=10,
                                   border_width=1,border_color=c['border'],fg_color=c['list_sel'],
                                   hover_color=c['hover'],text_color=c['fg'])
            self.run_button.pack(side='left',padx=(0,3));self.tooltip(self.run_button,'Run active file · F5')
            self.stop_button=icon_button(self,actions,'stop',self.stop_task,'Stop task · Ctrl+.',size=15,width=28,height=30)
            self.stop_button.pack(side='left',padx=(0,10))
            for icon,command,tip in [('panel-side',self.toggle_sidebar,'Toggle files · Ctrl+B'),
                                     ('panel-bottom',self.toggle_panel,'Toggle terminal · Ctrl+`'),
                                     ('chat',self.toggle_chat,'Toggle Friday chat'),
                                     ('sun',self.show_appearance,'Appearance and window settings')]:
                icon_button(self,actions,icon,command,tip,size=16,width=29,height=30).pack(side='left',padx=1)
            self._winctrl_zone=ctk.CTkFrame(actions,fg_color='transparent');self._winctrl_zone.pack(side='right',padx=(11,0))
            icon_button(self,self._winctrl_zone,'minimize',self.minimize_to_pill,'Minimize',size=14,width=27,height=30).pack(side='left')
            # One set of real controls in EVERY mode -- the IDE behaves
            # exactly like Friday's other panels now (the older
            # "only Friday may close it" placeholder rule is superseded):
            # maximize toggles, and the X really closes -- close_window()
            # keeps the one safeguard every close path shares (the
            # unsaved-changes prompt), and destroy() still tells Friday
            # via _agent_host.on_ide_closed(), so a human X can never
            # leave Friday pointing at a dead window.
            self._max_btn=icon_button(self,self._winctrl_zone,'maximize',self.toggle_maximize,'Maximize / restore',size=14,width=27,height=30)
            self._max_btn.pack(side='left')
            icon_button(self,self._winctrl_zone,'close',self.close_window,'Close IDE',size=13,width=27,height=30).pack(side='left')
            for widget in [bar,brand,mark]:
                widget.bind('<Button-1>',self._drag_start,add='+');widget.bind('<B1-Motion>',self._drag_move,add='+')
                widget.bind('<Double-Button-1>',lambda e:self.toggle_maximize(),add='+')
            bar.bind('<Configure>',self._header_layout,add='+')
        def _menu(self,items):
            c=core.C
            menu=tk.Menu(self,tearoff=0,bg=c['sidebar'],fg=c['fg'],activebackground=c['list_sel'],
                         activeforeground=c['fg'],disabledforeground=c['fg_faint'],bd=0,font=self.f_ui)
            for item in items:
                if item is None:menu.add_separator()
                else:menu.add_command(label=item[0],accelerator=item[1],command=item[2])
            return menu
        def _popup(self,menu,button):
            try:menu.tk_popup(button.winfo_rootx(),button.winfo_rooty()+button.winfo_height()+5)
            finally:menu.grab_release()
        def _header_layout(self,event=None):
            narrow=self.winfo_width()<1250*self.ui_scale
            for name,button in self._quiet_menu_buttons.items():
                if narrow and name not in ['File']:button.pack_forget()
                elif not button.winfo_manager():button.pack(side='left',before=self._quiet_more,padx=1)
        def _build_statusbar(self):
            self.status=StatusBar(self);self.status.pack(fill='x',side='bottom',padx=12,pady=(0,4))
        def _build_body(self):
            self.activity_bar=ActivityBar(self);self.activity_bar.pack(side='left',fill='y',padx=(12,0),pady=(0,12))
            self.body=tk.PanedWindow(self,orient='horizontal',bd=0,sashwidth=3,sashrelief='flat',
                                      bg=core.C['border_soft'],opaqueresize=True,showhandle=False)
            self.body.pack(side='left',fill='both',expand=True,padx=(0,12),pady=(0,12))
            self.sidebar=SideBar(self);self.sidebar_visible=True
            self.body.add(self.sidebar,minsize=170,width=232,stretch='never')
            self.vpanes=tk.PanedWindow(self.body,orient='vertical',bd=0,sashwidth=3,sashrelief='flat',
                                       bg=core.C['border_soft'],opaqueresize=True,showhandle=False)
            self.body.add(self.vpanes,minsize=310,stretch='always')
            self.editor=EditorArea(self);self.vpanes.add(self.editor,minsize=200,stretch='always')
            self.panel=Panel(self);self.panel_visible=False
            self.chat_panel=None
            self.chat_visible=self._agent_controlled or self._quiet_show_chat
            if self.chat_visible:
                self.chat_panel=ChatSidePanel(self)
                self.body.add(self.chat_panel,minsize=280,width=370,stretch='never')
                self._register_chat()
        def _register_chat(self):
            host=self._agent_host
            if host is None or self.chat_panel is None:return
            # Plain queue callbacks avoid retaining a dead renderer/Tk
            # interpreter in Friday's global notification handlers.
            results=self.chat_panel.state.results
            try:host.register_ide_chat_notify(lambda t,o=None:results.put(('__notify__',True,t,o)))
            except (AttributeError,TypeError):pass
            try:host.register_ide_build_status(lambda phase,status,detail:results.put(('__build__',True,(phase,status,detail),None)))
            except (AttributeError,TypeError):pass
            try:host.register_ide_task_steps(lambda kind,label,status,detail,sid=None:results.put(('__steps__',True,(kind,label,status,detail,sid),None)))
            except (AttributeError,TypeError):pass
        def toggle_chat(self):
            if self.chat_panel is None:
                self.chat_panel=ChatSidePanel(self);self._quiet_show_chat=True
                self.body.add(self.chat_panel,minsize=280,width=370,stretch='never')
                self.chat_visible=True;self._register_chat()
            elif self.chat_visible:
                self.body.forget(self.chat_panel);self.chat_visible=False
            else:
                self.body.add(self.chat_panel,minsize=280,width=370,stretch='never');self.chat_visible=True
        def cmdcenter_set(self,text):
            # Keep the original Entry-shaped field for tool/test callers.
            return BaseApp.cmdcenter_set(self,text)
        def _refresh_title(self):
            BaseApp._refresh_title(self)
            if hasattr(self,'cmdcenter'):
                self.cmdcenter_set(self.workspace_display_name())
            if hasattr(self,'status'):
                if not self.status.pos.cget('text') and self.current_buffer() is None:
                    self.status.pos.configure(text='Ready when you are')
                if getattr(self,'_quiet_project_label',None)!=self.root_dir:
                    self._quiet_project_label=self.root_dir
                    branch='Workspace'
                    if os.path.isdir(os.path.join(self.root_dir,'.git')):
                        try:
                            value=subprocess.run(['git','branch','--show-current'],cwd=self.root_dir,
                                                  capture_output=True,text=True,timeout=2).stdout.strip()
                            branch=value or 'Detached HEAD'
                        except (OSError,subprocess.TimeoutExpired):pass
                    self.status.branch.configure(text=branch)
        def get_terminal_output(self):
            self.panel.drain_available_output()
            return BaseApp.get_terminal_output(self)
        def show_appearance(self):
            top=self._dialog('Appearance',370,330);body=top.body;c=core.C
            Label(body,text='Keep it comfortable.',font=ui_font(self,17,'bold'),text_color=c['fg'],anchor='w').pack(fill='x',padx=20,pady=(17,12))
            for name in self.list_themes():
                action_button(self,body,'Theme: '+name.title(),lambda n=name:self.set_theme(n)).pack(fill='x',padx=20,pady=(0,7))
            row=ctk.CTkFrame(body,fg_color='transparent');row.pack(fill='x',padx=20,pady=(3,5))
            action_button(self,row,'Less opaque',lambda:self.fade(-.04),width=142).pack(side='left')
            action_button(self,row,'More opaque',lambda:self.fade(.04),width=142).pack(side='right')
            action_button(self,body,'Toggle always on top',self.toggle_topmost).pack(fill='x',padx=20,pady=5)
            top.activate()
        def _dialog(self,title,w,h,frameless=True):
            top=PanelToplevel(self);top.withdraw();top.title(title);top.configure(fg_color=core.C['sidebar'])
            top.overrideredirect(frameless);top.transient(self)
            x=max(0,min(self.winfo_rootx()+(self.winfo_width()-w)//2,self.winfo_screenwidth()-w))
            y=max(0,min(self.winfo_rooty()+max(40,(self.winfo_height()-h)//3),self.winfo_screenheight()-h))
            top.geometry(f'{w}x{h}+{x}+{y}')
            if frameless:
                header=ctk.CTkFrame(top,fg_color=core.C['panel'],height=39,corner_radius=0)
                header.pack(fill='x');header.pack_propagate(False)
                Label(header,text=title,font=ui_font(self,12,'bold'),text_color=core.C['fg']).pack(side='left',padx=16,pady=8)
                icon_button(self,header,'close',top.destroy,'Close dialog',size=13,width=29,height=29).pack(side='right',padx=8,pady=5)
            top.body=ctk.CTkFrame(top,fg_color=core.C['sidebar'],corner_radius=0);top.body.pack(fill='both',expand=True)
            def activate():
                top.deiconify();top.lift();top.update_idletasks();top.grab_set();top.focus_force()
                first=getattr(top,'focus_widget',None)
                if first is not None:first.focus_force()
            top.activate=activate;top.bind('<Escape>',lambda e:top.destroy())
            return top
        def _rebuild_ui(self):
            # Core owns buffer/terminal snapshotting; new chat/terminal
            # state is plain and shared, so pending results survive.
            self._quiet_rebuild_generation+=1
            generation=self._quiet_rebuild_generation
            if self._quiet_restore_job:self.after_cancel(self._quiet_restore_job)
            self._quiet_restore_job=None
            self._quiet_rebuilding=True
            self._stash()
            dirty={id(buf):buf.get('modified',False) for buf in self.buffers}
            view=self.editor.text
            position=view.index('insert');scroll=view.yview()[0]
            for buf in self.buffers:self._flush_agent_write(buf)
            for tip in self._quiet_tooltips:tip.hide()
            self._quiet_tooltips=[]
            if self._quiet_icons:self._quiet_icons.clear()
            for menu in self._quiet_menus:
                try:menu.destroy()
                except tk.TclError:pass
            self._quiet_menus=[]
            self._restore_native_root_configure()
            BaseApp._rebuild_ui(self)
            self._restore_native_root_configure()
            def restore():
                if self._quiet_closing or self._quiet_rebuilding or generation!=self._quiet_rebuild_generation:return
                self._quiet_restore_job=None
                for buf in self.buffers:buf['modified']=dirty.get(id(buf),buf.get('modified',False))
                try:self.editor.text.mark_set('insert',position);self.editor.text.yview_moveto(scroll)
                except tk.TclError:pass
                self.editor.redraw_tabs();self._refresh_title()
            self._quiet_restore_job=self.after_idle(restore);self._quiet_rebuilding=False
        def destroy(self):
            if self._quiet_closing:return
            self._quiet_closing=True
            while True:
                try:self._quiet_cross_thread.get_nowait()
                except Empty:break
            for tip in self._quiet_tooltips:tip.hide()
            # No new process/PTY behaviour: just stop existing managed
            # processes before their owning widget tree disappears.
            task=getattr(self,'task_proc',None)
            if task is not None:
                try:self.stop_task()
                except tk.TclError:pass
            panel=getattr(self,'panel',None)
            proc=getattr(panel,'proc',None)
            if proc is not None:
                try:proc.terminate()
                except OSError:pass
            bank=self._quiet_icons
            super().destroy()
            if bank is not None:bank.clear()
            # Native Tk roots don't themselves unsubscribe from CTk's
            # window registries when their last CTk child is gone.
            ctk.ScalingTracker.window_widgets_dict.pop(self,None)
            ctk.ScalingTracker.window_dpi_scaling_dict.pop(self,None)
            if self in ctk.AppearanceModeTracker.app_list:ctk.AppearanceModeTracker.app_list.remove(self)
            if not ctk.AppearanceModeTracker.app_list:ctk.AppearanceModeTracker.update_loop_running=False
            if not ctk.ScalingTracker.window_widgets_dict:ctk.ScalingTracker.update_loop_running=False
    return WorkspaceMixin
