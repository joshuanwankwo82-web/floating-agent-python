"""Calm navigation, Explorer/search views, terminal chrome and status bar.

Factories receive the original module explicitly: no circular imports or
new backend. Filesystem/editor/process behaviour is inherited from Friday.
"""
import os
import queue
import tkinter as tk
import customtkinter as ctk
from .widgets import _OwnedTimers, Label, Button, PanelScrollFrame, icon_button, action_button
from .tokens import ui_font


class TerminalState:
    def __init__(self):
        self.proc=None
        self.history=[]
        self.hidx=0
        self.q=queue.Queue()


def make_panels(core):
    BaseSideBar,BasePanel=core.SideBar,core.Panel
    class ActivityBar(ctk.CTkFrame):
        _ITEMS=core.ActivityBar._ITEMS
        def __init__(self,app):
            c=core.C
            super().__init__(app,fg_color=c['activity'],width=52,corner_radius=0)
            self.app=app;self.pack_propagate(False);self._buttons={};self._bars={}
            top=ctk.CTkFrame(self,fg_color='transparent',corner_radius=0)
            top.pack(fill='x',pady=(12,0))
            for name,tip in self._ITEMS:
                row=ctk.CTkFrame(top,fg_color='transparent',corner_radius=0)
                row.pack(fill='x',pady=4)
                marker=tk.Frame(row,width=2,bg=c['activity'])
                marker.pack(side='left',fill='y',pady=8)
                b=icon_button(app,row,name,lambda n=name:app.show_sidebar_mode(n),
                              tip,size=20,width=38,height=38)
                b.pack(side='left',padx=6)
                self._buttons[name]=b;self._bars[name]=marker
            bottom=ctk.CTkFrame(self,fg_color='transparent')
            bottom.pack(side='bottom',fill='x',pady=14)
            icon_button(app,bottom,'gear',lambda:app.quick_open('>'),
                        'Commands and appearance',size=19,width=38,height=38).pack(pady=4)
        def set_active(self,mode):
            c=core.C
            for name,button in self._buttons.items():
                selected=name==mode
                button.configure(fg_color=c['list_sel'] if selected else 'transparent',
                                 image=self.app._quiet_icons(name,c['fg'] if selected else c['fg_dim'],20))
                self._bars[name].configure(bg=c['accent'] if selected else c['activity'])

    class SideBar(_OwnedTimers,core.SideBar):
        def __init__(self,app):
            super().__init__(app)
            self.configure(highlightthickness=0)
            self.body.destroy()
            self.body=ctk.CTkFrame(self,fg_color=core.C['sidebar'],corner_radius=0)
        def show(self,mode):
            # Friday's original view builders were hidden rather than
            # destroyed. Retain the shared Explorer body, not stale
            # search/git/header widgets from previous navigation.
            for child in list(self.winfo_children()):
                if child is not self.body:child.destroy()
            for bar in self._guide_bars:
                if bar.winfo_exists():bar.place_forget()
            return super().show(mode)
        def _title(self,text,actions=()):
            titles={'EXPLORER':'Files','SEARCH':'Search','RUN AND DEBUG':'Run & tasks',
                    'SOURCE CONTROL':'Source control','EXTENSIONS':'Extensions'}
            bar=ctk.CTkFrame(self,fg_color=core.C['sidebar'],height=48,corner_radius=0)
            bar.pack(fill='x',pady=(5,0));bar.pack_propagate(False)
            Label(bar,text=titles.get(text,text),font=ui_font(self.app,12,'bold'),
                  text_color=core.C['fg'],anchor='w').pack(side='left',padx=(17,4))
            for name,tip,command in reversed(actions):
                icon_button(self.app,bar,name,command,tip,size=15,width=26,height=28).pack(side='right',padx=1)
        def _make_no_folder_frame(self):
            app=self.app;c=core.C
            outer=ctk.CTkFrame(self.body,fg_color='transparent')
            Label(outer,text='NO FOLDER OPENED',font=ui_font(app,10,'bold'),text_color=c['fg_dim'],anchor='w').pack(fill='x',padx=18,pady=(18,12))
            for text in ['You have not yet opened a folder.']:
                Label(outer,text=text,font=ui_font(app,12),text_color=c['fg_dim'],
                      wraplength=200,justify='left',anchor='w').pack(fill='x',padx=18,pady=(0,14))
            action_button(app,outer,'Open Folder',app.open_folder_dialog,primary=True,icon='folder').pack(fill='x',padx=18,pady=(0,22))
            Label(outer,text='You can clone a repository locally.',font=ui_font(app,12),
                  text_color=c['fg_dim'],wraplength=200,justify='left',anchor='w').pack(fill='x',padx=18,pady=(0,12))
            action_button(app,outer,'Clone Repository',app.clone_repository_dialog,icon='branch').pack(fill='x',padx=18)
            Label(outer,text='A little room for your next project.',font=ui_font(app,11),
                  text_color=c['fg_faint'],wraplength=200,justify='left',anchor='w').pack(fill='x',padx=18,pady=24)
            return outer
        def _build_search(self):
            app=self.app;c=core.C
            frame=ctk.CTkFrame(self,fg_color='transparent')
            frame.pack(fill='both',expand=True,padx=15,pady=7)
            self.search_scope=getattr(self,'search_scope',None)
            Label(frame,text='Find in workspace',font=ui_font(app,11),text_color=c['fg_dim'],anchor='w').pack(fill='x',pady=(4,7))
            self.s_query=ctk.CTkEntry(frame,placeholder_text='Search files…',font=ui_font(app,12),
                                     height=36,fg_color=c['input'],text_color=c['fg'],
                                     border_color=c['border'],corner_radius=9)
            self.s_query.pack(fill='x');self.s_query.bind('<Return>',lambda e:self._run_search())
            Label(frame,text='Replace with',font=ui_font(app,11),text_color=c['fg_dim'],anchor='w').pack(fill='x',pady=(17,7))
            self.s_repl=ctk.CTkEntry(frame,placeholder_text='Replacement text',font=ui_font(app,12),
                                    height=36,fg_color=c['input'],text_color=c['fg'],
                                    border_color=c['border'],corner_radius=9)
            self.s_repl.pack(fill='x')
            action_button(app,frame,'Find',self._run_search,icon='search').pack(fill='x',pady=(12,7))
            action_button(app,frame,'Replace All',self._replace_all).pack(fill='x')
            self.s_count=Label(frame,text='',font=ui_font(app,10),text_color=c['fg_faint'],anchor='w')
            self.s_count.pack(fill='x',pady=(16,5))
            self.s_results=PanelScrollFrame(frame,fg_color='transparent',corner_radius=0,
                                            label_font=ui_font(app,11),
                                            scrollbar_button_color=c['border'],
                                            scrollbar_button_hover_color=c['scroll_hover'])
            self.s_results.pack(fill='both',expand=True)
        def _run_search(self):
            query=self.s_query.get()
            if not query:return
            for child in self.s_results.winfo_children():child.destroy()
            scope=getattr(self,'search_scope',None) or self.app.root_dir
            hits=self.app.fs.search(query,scope=scope)
            self._search_hits=[(self.app.fs.resolve(rel),line,text) for rel,line,text in hits]
            self.s_count.configure(text=f'{len(hits)} results in workspace')
            for path,line,text in self._search_hits[:80]:
                row=ctk.CTkFrame(self.s_results,fg_color=core.C['input'],corner_radius=9)
                row.pack(fill='x',pady=(0,6))
                rel=os.path.relpath(path,self.app.root_dir)
                title=Label(row,text=f'{rel}:{line}',font=ui_font(self.app,11),
                            text_color=core.C['fg'],anchor='w',wraplength=190)
                title.pack(fill='x',padx=9,pady=(8,3))
                preview=Label(row,text=text[:100],font=ui_font(self.app,10),
                              text_color=core.C['fg_dim'],anchor='w',justify='left',wraplength=185)
                preview.pack(fill='x',padx=9,pady=(0,8))
                for widget in [row,title,preview]:widget.bind('<Button-1>',lambda e,p=path,n=line:self.app.open_file(p,line=n))
        def _replace_all(self):
            if not self.s_query.get():return
            # The existing implementation owns actual replace behaviour.
            return BaseSideBar._replace_all(self)
        def _build_run(self):
            c=core.C;app=self.app
            frame=ctk.CTkFrame(self,fg_color='transparent')
            frame.pack(fill='both',expand=True,padx=17,pady=12)
            Label(frame,text='Make something work.',font=ui_font(app,16,'bold'),
                  text_color=c['fg'],anchor='w',wraplength=205).pack(fill='x',pady=(5,10))
            Label(frame,text='Run the active file, start a project, or choose a detected task.',
                  font=ui_font(app,12),text_color=c['fg_dim'],anchor='w',justify='left',wraplength=205).pack(fill='x',pady=(0,20))
            for label,icon,command in [('Run active file','play',app.run_file),('Run project','play',app.run_project),
                                       ('Build project','settings',app.build_project),('Choose task…','terminal',app.show_tasks),
                                       ('Stop task','stop',app.stop_task)]:
                action_button(app,frame,label,command,icon=icon).pack(fill='x',pady=(0,8))
            Label(frame,text='Uses your installed toolchains. F6 runs the debugger for the current Python file; click the gutter (or F9) to set breakpoints.',
                  font=ui_font(app,10),text_color=c['fg_faint'],justify='left',wraplength=205).pack(fill='x',pady=15)
        def _build_extensions(self):
            c=core.C;app=self.app
            frame=ctk.CTkFrame(self,fg_color='transparent');frame.pack(fill='both',expand=True,padx=17,pady=12)
            Label(frame,text='Extensions are not connected.',font=ui_font(app,14,'bold'),text_color=c['fg'],
                  anchor='w',justify='left',wraplength=205).pack(fill='x',pady=(4,10))
            Label(frame,text='Friday does not have an extension installer. These are examples, not installed features.',
                  font=ui_font(app,12),text_color=c['fg_dim'],wraplength=205,justify='left').pack(fill='x',pady=(0,15))
            for name,publisher in [('Python','Microsoft'),('Pylance','Microsoft'),('Jupyter','Microsoft'),('Prettier','Prettier'),('GitLens','GitKraken')]:
                card=ctk.CTkFrame(frame,fg_color=c['input'],corner_radius=10);card.pack(fill='x',pady=(0,7))
                Label(card,text=name,font=ui_font(app,12),text_color=c['fg'],anchor='w').pack(fill='x',padx=11,pady=(8,1))
                Label(card,text=publisher+' · Example',font=ui_font(app,10),text_color=c['fg_faint'],anchor='w').pack(fill='x',padx=11,pady=(0,8))
        def _build_git(self):
            c=core.C;app=self.app
            frame=ctk.CTkFrame(self,fg_color='transparent');frame.pack(fill='both',expand=True,padx=17,pady=12)
            if not os.path.isdir(os.path.join(app.root_dir,'.git')):
                Label(frame,text='A fresh start.',font=ui_font(app,16,'bold'),text_color=c['fg'],anchor='w').pack(fill='x',pady=(6,10))
                Label(frame,text='Initialize Git here to track your work.',font=ui_font(app,12),text_color=c['fg_dim'],
                      wraplength=205,justify='left').pack(fill='x',pady=(0,18))
                action_button(app,frame,'Initialize Repository',app.git_init_repository,icon='branch').pack(fill='x')
                self._quiet_friday_github(app,core,frame,repo=False)   # F42
                return
            changes=app._raw_git_status()
            Label(frame,text=f'CHANGES ({len(changes)})',font=ui_font(app,10,'bold'),text_color=c['fg_dim'],anchor='w').pack(fill='x',pady=(0,10))
            if not changes:
                Label(frame,text='Your workspace is clean.',font=ui_font(app,12),text_color=c['fg_dim'],anchor='w',wraplength=205).pack(fill='x')
            for path,code in sorted(changes.items()):
                row=ctk.CTkFrame(frame,fg_color=c['input'],corner_radius=9);row.pack(fill='x',pady=(0,6))
                Label(row,text=code,font=ui_font(app,10,'bold'),width=23,text_color=c['warn'] if code=='M' else c['success']).pack(side='left',padx=(5,0))
                rel=os.path.relpath(path,app.root_dir)
                label=Label(row,text=rel,font=ui_font(app,11),text_color=c['fg'],anchor='w',wraplength=178)
                label.pack(fill='x',expand=True,padx=7,pady=9)
                for widget in [row,label]:widget.bind('<Button-1>',lambda e,p=path:app.open_file(p) if os.path.isfile(p) else None)
            self._quiet_friday_github(app,core,frame,repo=True)   # F42
        def _quiet_friday_github(self,app,core,frame,repo):
            """F42: Settings-profile-driven GitHub actions -- the live
            (quiet-skin) half of the IDE<->Settings bridge. Identities and
            the github.com token stay owned by Friday's Settings; these
            buttons spend them through git_bridge (never shown tokens)."""
            c=core.C;bridge=app._git_bridge()
            ctk.CTkFrame(frame,fg_color=c['border'],height=1).pack(fill='x',pady=(14,12))
            Label(frame,text='FRIDAY GITHUB',font=ui_font(app,10,'bold'),text_color=c['fg_dim'],anchor='w').pack(fill='x',pady=(0,4))
            identity='Commits as: you pick the author on each commit/push'
            remote=''
            if repo and bridge and bridge.is_repo(app.root_dir):
                remote=bridge.remote_url(app.root_dir)
                suggested=bridge.suggested_author(remote,bridge.authors())
                if suggested:
                    # F46: clone URL carries HIS username -> auto author
                    identity=('Commits as: '+suggested['username']
                              +' automatically (this clone is yours -- no asking)')
            Label(frame,text=identity,font=ui_font(app,10),text_color=c['fg_faint'],anchor='w',justify='left',wraplength=205).pack(fill='x',pady=(0,8))
            if repo:
                action_button(app,frame,'Commit & Push'+chr(8230),app.github_commit_push_dialog,primary=True,icon='check').pack(fill='x',pady=(0,8))
                action_button(app,frame,'Commit'+chr(8230),app.github_commit_dialog,icon='check').pack(fill='x',pady=(0,8))
                action_button(app,frame,'Push',app.github_push,icon='arrow_up').pack(fill='x',pady=(0,8))
                action_button(app,frame,'Pull',app.github_pull,icon='download').pack(fill='x',pady=(0,8))
                if bridge and bridge.parse_github_url(remote):
                    action_button(app,frame,'Open Pull Request'+chr(8230),app.github_pr_dialog,icon='clip').pack(fill='x',pady=(0,8))
                if bridge and not bridge.has_remote(app.root_dir):
                    action_button(app,frame,'Publish to GitHub'+chr(8230),app.github_publish_dialog,icon='spark').pack(fill='x',pady=(0,8))
            else:
                action_button(app,frame,'Clone Repository'+chr(8230),app.clone_repository_dialog,icon='download').pack(fill='x',pady=(8,0))
                action_button(app,frame,'Publish Workspace to GitHub'+chr(8230),app.github_publish_dialog,icon='spark').pack(fill='x',pady=(8,0))
            Label(frame,text='Push auth uses your github.com token from Settings > Profile > Web passwords; commit authors from Profile > GitHub authors.',
                  font=ui_font(app,10),text_color=c['fg_faint'],wraplength=205,justify='left').pack(fill='x',pady=(12,0))

    class Panel(_OwnedTimers,core.Panel):
        @property
        def proc(self):return self._shared.proc
        @proc.setter
        def proc(self,value):self._shared.proc=value
        @property
        def history(self):return self._shared.history
        @history.setter
        def history(self,value):self._shared.history=value
        @property
        def hidx(self):return self._shared.hidx
        @hidx.setter
        def hidx(self,value):self._shared.hidx=value
        def __init__(self,app):
            state=getattr(app,'_quiet_terminal_state',None)
            new=state is None
            self._shared=state or TerminalState();app._quiet_terminal_state=self._shared
            proc,hist,hidx,q=self._shared.proc,self._shared.history,self._shared.hidx,self._shared.q
            self._quiet_ready=False
            super().__init__(app)
            self.proc=proc;self.history=hist;self.hidx=hidx;self.q=q
            self.configure(highlightthickness=0)
            old=[w for w in self.winfo_children() if isinstance(w,ctk.CTkFrame)]
            for widget in old:widget.destroy()
            self.header=ctk.CTkFrame(self,fg_color=core.C['panel'],height=42,corner_radius=0)
            anchor=getattr(self,'_content',self.term)
            self.header.pack(fill='x',before=anchor);self.header.pack_propagate(False)
            self._tabs={};self._tab_rules={}
            for name in ['TERMINAL','OUTPUT','PROBLEMS','DEBUG CONSOLE']:
                b=Button(self.header,text=name.title(),font=ui_font(app,11),width=65,height=28,corner_radius=8,
                         fg_color='transparent',hover_color=core.C['hover'],text_color=core.C['fg_dim'],command=lambda n=name:self._sel(n))
                b.pack(side='left',padx=(10 if name=='TERMINAL' else 2,0),pady=7)
                self._tabs[name]=b
            icon_button(app,self.header,'close',app.toggle_panel,'Hide terminal',size=15,width=28,height=28).pack(side='right',padx=(1,12),pady=7)
            icon_button(app,self.header,'trash',self.clear,'Clear terminal',size=15,width=28,height=28).pack(side='right',pady=7)
            self._quiet_ready=True
            self._sel('TERMINAL')
            self.term.configure(font=(app.f_mono[0],11),padx=17,pady=8)
        def _swap_page(self,name):
            # After this class's header restyling, the base class's
            # paged PROBLEMS/OUTPUT/DEBUG CONSOLE content area must
            # still react to tab clicks: same pack-swap the base _sel
            # performs (pages live in self._pages inside self._content).
            for key,page in getattr(self,'_pages',{}).items():
                try:page.pack_forget()
                except tk.TclError:continue
            page=getattr(self,'_pages',{}).get(name)
            if page is not None:page.pack(fill='both',expand=True)
            if name=='OUTPUT':self.render_output()
            elif name=='PROBLEMS':self.render_problems()

        def drain_available_output(self):
            # An agent may request a snapshot immediately after a
            # command completes, before the next periodic 80ms draw.
            # Apply the real queued output on the GUI owner first.
            while True:
                try:item=self.q.get_nowait()
                except queue.Empty:break
                # app.log_output() sends 3-tuples routed to OUTPUT channels
                if (isinstance(item,tuple) and len(item)==3
                        and item[0]=='__OUT__'):
                    self.app._record_output(item[1],item[2]);continue
                text,tag=item
                if text=='\x00PROMPT':self._prompt()
                else:self.write(text,tag)
        def _sel(self,name):
            if not getattr(self,"_quiet_ready",False):
                self.active=name
                for key,label in self._tabs.items():
                    label.configure(fg=core.C['fg'] if key==name else core.C['fg_dim'])
                self._swap_page(name)
                return
            self.active=name
            for key,label in self._tabs.items():
                label.configure(fg_color=core.C['list_sel'] if key==name else 'transparent',
                                text_color=core.C['fg'] if key==name else core.C['fg_dim'])
            self._swap_page(name)

    class StatusBar(_OwnedTimers,core.StatusBar):
        H=29
        def __init__(self,app):
            # Retain the status fields called by every original app/tool.
            tk.Frame.__init__(self,app,bg=core.C['statusbar'],height=self.H,highlightthickness=0)
            self._owned_after_jobs=set();self._timer_widget_closed=False
            self.app=app;self.pack_propagate(False);self._agent_clear_job=None
            self.branch=self._item('Workspace',side='left')
            self.sync=self._item('',side='left');self.errors=self._item('',side='left')
            self.task=self._item('',side='left');self.agent=self._item('',side='left')
            self.pos=self._item('',side='right');self.lang=self._item('',side='right')
            self.spaces=self._item('Spaces: 4',side='right');self.enc=self._item('UTF-8',side='right')
            self.zoom=self._item(f'{round(app.font_size/core.EDITOR_FONT_SIZE*100)}%',side='right')
            # Less frequently used values are still live fields/actions,
            # tucked into a small appearance popover instead of noise.
            self.opacity=Label(self,text=f'Opacity {round(app.opacity*100)}%',font=ui_font(app,10),text_color=core.C['fg_dim'])
            self.theme=Label(self,text=f'Theme: {app.theme_name.title()}',font=ui_font(app,10),text_color=core.C['fg_dim'])
            self.zoom.bind('<Button-1>',lambda e:app.reset_zoom())
            self.lang.bind('<Button-1>',lambda e:app.quick_open('>'))
        def _item(self,text,side):
            label=Label(self,text=text,font=ui_font(self.app,10),height=20,text_color=core.C['status_fg'],fg_color=core.C['statusbar'],anchor='w')
            label.pack(side=side,padx=(12,4) if side=='left' else 7,pady=4)
            return label
        def set_agent_status(self,text,kind='write'):
            self.agent.configure(text=text,text_color=core.C['fg'],fg_color=core.C['list_sel'],corner_radius=6)
            if self._agent_clear_job:self.after_cancel(self._agent_clear_job)
            self._agent_clear_job=self.after(6000,self.clear_agent_status)
        def clear_agent_status(self):
            self._agent_clear_job=None
            if self.winfo_exists():self.agent.configure(text='',fg_color=core.C['statusbar'])

    return ActivityBar,SideBar,Panel,StatusBar
