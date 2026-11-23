"""Quiet Chat's visual language on Friday's real embedded IDE chat channel.

The existing host.ask / proactive messages / inline permissions / build
status contract is retained. Plain conversation state and the completion
queue live on the app, independent of a widget tree or theme rebuild.
"""
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from uuid import uuid4
import time
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk
from .icons import IconBank
from .tokens import ui_font
from .widgets import _OwnedTimers, Label, Button, Composer, PanelScrollFrame, Tooltip, icon_button


@dataclass
class ChatState:
    messages: list = field(default_factory=list)
    results: Queue = field(default_factory=Queue)
    pending_id: str | None = None
    draft: str = ''
    references: list = field(default_factory=list)
    options: list = field(default_factory=list)
    build: dict = field(default_factory=dict)
    # Live task-progress trace: ordered step rows the agent reports while
    # it works a task (write/run/verify/debug/...), plus the task title
    # and its start/finish markers. Survives theme rebuilds.
    steps: list = field(default_factory=list)
    task_title: str = ''
    task_started: float | None = None
    task_finished: str = ''


class ChatLog(PanelScrollFrame):
    def see(self,index='end'):
        self._parent_canvas.yview_moveto(1.0)


def make_chat(core):
    class ChatSidePanel(_OwnedTimers,ctk.CTkFrame):
        _STATUS_PHASE_LABELS=(('frontend','Frontend'),('backend','Backend'),('storage','Storage/Database'))
        def __init__(self,app):
            c=core.C
            super().__init__(app,fg_color=c['chat'],corner_radius=0)
            self.app=app;self._closed=False
            if not hasattr(app,'_quiet_core'):app._quiet_core=core
            if not hasattr(app,'_quiet_icons'):app._quiet_icons=IconBank(app)
            if not hasattr(app,'tooltip'):
                app._quiet_tooltips=[]
                def tip(widget,text):app._quiet_tooltips.append(Tooltip(widget,text,app))
                app.tooltip=tip
            state=getattr(app,'_quiet_chat_state',None)
            self.state=state or ChatState();app._quiet_chat_state=self.state
            self._pending_bubble=self._decision_row=self._status_row=None
            self._steps_row=None;self._step_labels={};self._steps_head=None;self._steps_sub=None
            self._status_labels={};self._rows={};self._wrap_labels=[]
            self._placeholder='Ask Friday about your workspace…'
            self._showing_placeholder=False
            self.header=ctk.CTkFrame(self,fg_color=c['chat'],height=75,corner_radius=0)
            self.header.pack(fill='x');self.header.pack_propagate(False)
            self.header.grid_columnconfigure(1,weight=1)
            mark=ctk.CTkFrame(self.header,width=33,height=33,corner_radius=11,fg_color=c['panel'],
                              border_width=1,border_color=c['border'])
            mark.grid(row=0,column=0,rowspan=2,padx=(18,10),pady=(19,14));mark.grid_propagate(False)
            Label(mark,text='✧' if app._quiet_icons('spark',c['accent'],19) is None else '',
                  image=app._quiet_icons('spark',c['accent'],19),font=ui_font(app,12),text_color=c['accent']).place(relx=.5,rely=.5,anchor='center')
            Label(self.header,text='Friday',font=ui_font(app,16,'bold'),text_color=c['fg'],anchor='w',height=21).grid(row=0,column=1,sticky='w',pady=(16,0))
            self.state_label=Label(self.header,text='Preview · no AI service' if getattr(app._agent_host,'is_preview',False) else 'Workspace agent' if app._agent_host is not None else 'Agent not connected',
                                   font=ui_font(app,10),text_color=c['fg_faint'],height=15,anchor='w')
            self.state_label.grid(row=1,column=1,sticky='w',pady=(0,14))
            icon_button(app,self.header,'download',self.export_transcript,'Export conversation',size=16,width=28,height=28).grid(row=0,column=2,rowspan=2,padx=(0,15))
            ctk.CTkFrame(self,fg_color=c['border_soft'],height=1,corner_radius=0).pack(fill='x',padx=17)
            self.footer=Label(self,text='Enter to send · Shift + Enter for a new line',font=ui_font(app,9),text_color=c['fg_faint'],height=23)
            self.footer.pack(side='bottom',fill='x',pady=(0,10))
            self.composer=ctk.CTkFrame(self,fg_color=c['input'],corner_radius=15,
                                     border_width=1,border_color=c['border'])
            self.composer.pack(side='bottom',fill='x',padx=15,pady=(0,5))
            self.composer.grid_columnconfigure(0,weight=1)
            self.entry=Composer(self.composer,height=58,fg_color='transparent',font=ui_font(app,13),
                                text_color=c['fg'],border_spacing=0,activate_scrollbars=False,
                                corner_radius=0,border_width=0,wrap='word',undo=True)
            self.entry.grid(row=0,column=0,sticky='ew',padx=13,pady=(12,1))
            _BLUE = '#3B82F6'
            self.entry._textbox.configure(insertbackground=_BLUE,insertwidth=2,
                                          insertontime=600,insertofftime=450,
                                           selectbackground=c['border'])
            self.placeholder=Label(self.composer,text=self._placeholder,font=ui_font(app,13),
                                    text_color=c['fg_faint'],fg_color=c['input'],height=22)
            self.placeholder.bind('<Button-1>',lambda e:self.entry.focus_set())
            self.entry.bind('<Return>',self._send,add='+');self.entry.bind('<KP_Enter>',self._send,add='+')
            self.entry.bind('<<Modified>>',self._modified,add='+')
            _glow = self.composer
            _inner = self.entry._textbox  # the real tk.Text -- events land here
            _inner.bind('<FocusIn>',lambda e:_glow.configure(border_color=_BLUE),add='+')
            _inner.bind('<FocusOut>',lambda e:_glow.configure(border_color=c['border']),add='+')
            _inner.bind('<Button-1>',lambda e:_glow.configure(border_color=_BLUE),add='+')
            # Keep root editor shortcuts from stealing input editing.
            for sequence,fn in [('<Control-a>',self._select_draft),('<Control-z>',self._undo_draft),
                                 ('<Control-y>',self._redo_draft),('<Control-f>',lambda e:'break'),
                                 ('<Alt-Up>',lambda e:'break'),('<Alt-Down>',lambda e:'break'),
                                 ('<Shift-Alt-Down>',lambda e:'break')]:
                self.entry.bind(sequence,fn,add='+')
            toolbar=ctk.CTkFrame(self.composer,fg_color='transparent')
            toolbar.grid(row=1,column=0,sticky='ew',padx=9,pady=(0,9));toolbar.grid_columnconfigure(1,weight=1)
            self.attach_button=icon_button(app,toolbar,'clip',self.attach_files,'Add workspace file references · not an upload',size=16,width=28,height=28)
            self.attach_button.grid(row=0,column=0)
            self.reference_label=Label(toolbar,text='Add context',font=ui_font(app,10),text_color=c['fg_faint'],anchor='w')
            self.reference_label.grid(row=0,column=1,sticky='w',padx=3)
            self.reference_label.bind('<Button-1>',lambda e:self.clear_references() if self.state.references else self.attach_files())
            self._mic_rec=None
            # worker-thread transcripts must never touch Tk directly --
            # they queue here and the existing 60ms _poll applies them
            self._mic_q=Queue()
            self.mic_button=icon_button(app,toolbar,'mic',self._toggle_mic,
                                       'Voice input · click to record, click again to transcribe (review + Enter to send)',size=15,width=28,height=28)
            self.mic_button.grid(row=0,column=2,padx=(1,2))
            image=app._quiet_icons('arrow_up',c['editor'],17)
            self.send_button=Button(toolbar,text='↑' if image is None else '',image=image,font=ui_font(app,12),
                                     command=self._send,width=33,height=33,corner_radius=10,
                                     fg_color=c['accent'],hover_color=core.mix(c['accent'],'#ffffff',.1),text_color=c['editor'])
            self.send_button.grid(row=0,column=3,padx=(3,2))
            app.tooltip(self.send_button,'Send message · Enter')
            suggestions=ctk.CTkFrame(self,fg_color='transparent')
            suggestions.pack(side='bottom',fill='x',padx=17,pady=(7,12))
            for text,prompt in [('Explain this file','Explain the active file in this workspace.'),('Review changes','Review the current workspace changes.')]:
                Button(suggestions,text=text,font=ui_font(app,10),height=27,width=114,corner_radius=13,
                       fg_color='transparent',hover_color=c['input'],border_width=1,border_color=c['border'],
                       text_color=c['fg_dim'],command=lambda p=prompt:self.use_prompt(p)).pack(side='left',padx=(0,6))
            self.log=ChatLog(self,fg_color='transparent',corner_radius=0,label_font=ui_font(app,11),
                              scrollbar_fg_color=c['chat'],scrollbar_button_color=c['border_soft'],
                              scrollbar_button_hover_color=c['border'])
            self.log.pack(fill='both',expand=True,padx=(15,9),pady=(5,0));self.log.grid_columnconfigure(0,weight=1)
            self.log._parent_canvas.bind('<Configure>',self._wrap,add='+')
            if not self.state.messages:
                self.state.messages.append({'id':uuid4().hex,'role':'friday','text':'A little room to think.\n\nAsk me to build, edit, or explain something in this workspace.',
                                            'pending':False,'error':False})
            self._render_all()
            self.entry.insert(0,self.state.draft)
            self._update_composer()
            self._poll_job=self.after(60,self._poll)
        def _font(self,size=12,weight='normal'):return ui_font(self.app,size,weight)
        def _show_placeholder(self):
            self._showing_placeholder=False;self._update_composer()
        def _clear_placeholder(self,event=None):
            self._showing_placeholder=False;self.placeholder.place_forget()
        def _maybe_restore_placeholder(self,event=None):self._update_composer()
        def _modified(self,event=None):
            if self.entry._textbox.edit_modified():
                self.entry._textbox.edit_modified(False);self._update_composer()
        def _update_composer(self):
            text=self.entry.get();self.state.draft=text
            if text:self.placeholder.place_forget()
            else:self.placeholder.place(x=14,y=12)
            enabled=bool(text.strip() or self.state.references) and self.state.pending_id is None
            self.send_button.configure(state='normal' if enabled else 'disabled',
                                       fg_color=core.C['accent'] if enabled else core.C['border'])
            self.reference_label.configure(text=(self.state.references[0].rsplit('/',1)[-1]+' ×') if self.state.references else 'Add context')
            self.state_label.configure(text='Waiting for Friday…' if self.state.pending_id else
                ('Preview · no AI service' if getattr(self.app._agent_host,'is_preview',False) else 'Workspace agent' if getattr(self.app,'_agent_host',None) is not None else 'Agent not connected'))
        def _select_draft(self,event=None):
            self.entry._textbox.tag_add('sel','1.0','end-1c');return 'break'
        def _undo_draft(self,event=None):
            try:self.entry._textbox.edit_undo()
            except tk.TclError:pass
            return 'break'
        def _redo_draft(self,event=None):
            try:self.entry._textbox.edit_redo()
            except tk.TclError:pass
            return 'break'
        def use_prompt(self,prompt):
            self.entry.delete(0,'end');self.entry.insert(0,prompt);self._update_composer();self.entry.focus_set()
        def attach_files(self):
            paths=filedialog.askopenfilenames(parent=self.app,initialdir=getattr(self.app,'root_dir',None),title='Add workspace file references')
            references=[]
            for path in paths:
                if hasattr(self.app,'fs'):
                    try:
                        full=self.app.fs.resolve(path);references.append(str(Path(full).relative_to(self.app.fs.root)).replace('\\','/'))
                    except (PermissionError,ValueError):
                        self.notice('Choose files inside the open workspace. Files are not read or uploaded by this control.')
                else:references.append(Path(path).name)
            self.state.references=list(dict.fromkeys(self.state.references+references));self._update_composer()
            if references:self.notice('Workspace references added. No file contents were read or uploaded.')
        def clear_references(self):self.state.references=[];self._update_composer()
        def _send(self,event=None):
            if event is not None and getattr(event,'state',0)&1:return None
            text=self.entry.get().strip()
            if self.state.pending_id:return 'break'
            if not text and not self.state.references:return 'break'
            if self.state.references:
                text+='\n\nWorkspace file references (not uploads): '+', '.join(self.state.references)
            self.state.references=[];self.entry.delete(0,'end');self.state.draft=''
            self._submit_text(text);self._update_composer();return 'break'
        def _submit_text(self,text):
            if not text or self.state.pending_id:return
            # A fresh user turn opens a fresh step-trace card (the old
            # one stays readable above as record of earlier work). Done
            # before the bubble so late steps from the previous turn can
            # never land on the new card.
            self.state.steps=[];self.state.task_finished=''
            self.state.task_title=text.splitlines()[0][:64]
            self.state.task_started=time.monotonic()
            self._clear_steps_card()
            self._dismiss_decision_options();self._add_bubble('you',text)
            identifier=uuid4().hex
            self.state.pending_id=identifier
            row,label=self._add_bubble('friday','Friday is thinking…',pending=True,identifier=identifier)
            self._pending_bubble=(row,label);self._update_composer()
            host=getattr(self.app,'_agent_host',None)
            if host is None:
                self._resolve_pending("Friday's chat channel isn't available this run — launch Friday to use the workspace agent.",False)
                return
            # Provider callbacks retain queue + plain ID, not this Tk
            # view. A theme rebuild can safely replace the renderer.
            results=self.state.results
            def on_done(ok,result,options=None):results.put((identifier,ok,result,options))
            try:host.ask(text,on_done)
            except Exception as error:results.put((identifier,False,str(error),None))
        def _poll(self):
            if self._closed:return
            while True:
                try:text,err=self._mic_q.get_nowait()
                except Empty:break
                self._on_mic_transcript(text,err)
            while True:
                try:identifier,ok,text,options=self.state.results.get_nowait()
                except Empty:break
                if identifier=='__notify__':self.receive_proactive_message(text,options)
                elif identifier=='__build__':self.update_build_status(*text)
                elif identifier=='__steps__':self.update_task_step(*text)
                elif identifier==self.state.pending_id:self._resolve_pending(text,ok,options)
            self._poll_job=self.after(60,self._poll)
        def _resolve_pending(self,text,ok,options=None):
            identifier=self.state.pending_id
            if identifier:
                record=next((m for m in self.state.messages if m['id']==identifier),None)
                if record:
                    record.update(text=str(text or ''),pending=False,error=not ok)
                    old=self._rows.pop(identifier,None)
                    if old is not None:old[0].destroy()
                    index=self.state.messages.index(record)
                    self._render_message(record,index)
            self.state.pending_id=None;self._pending_bubble=None
            self._finish_task_steps(ok)
            if ok and options:self._show_decision_options(options)
            self._update_composer();self._scroll_end()
        def _add_bubble(self,role,text,pending=False,identifier=None):
            record={'id':identifier or uuid4().hex,'role':role,'text':str(text),
                    'pending':pending,'error':False}
            self.state.messages.append(record)
            result=self._render_message(record,len(self.state.messages)-1)
            self._position_cards();self._scroll_end();return result
        def _render_message(self,record,index):
            c=core.C
            row=ctk.CTkFrame(self.log,fg_color='transparent',corner_radius=0)
            row.grid(row=index,column=0,sticky='ew',pady=(12,8));row.grid_columnconfigure(0,weight=1)
            user=record['role']=='you'
            if user:
                name=Label(row,text='You',font=self._font(10),text_color=c['fg_faint'],height=16)
                name.grid(row=0,column=0,sticky='e',padx=7,pady=(0,6))
                body=ctk.CTkFrame(row,fg_color=c['list_sel'],corner_radius=13,border_width=1,border_color=c['border'])
                body.grid(row=1,column=0,sticky='e',padx=(25,3))
                label=Label(body,text=record['text'],font=self._font(13),text_color=c['fg'],
                            justify='left',anchor='w',height=1,wraplength=245)
                label.pack(padx=13,pady=11);self._wrap_labels.append((label,'user'))
            else:
                head=ctk.CTkFrame(row,fg_color='transparent');head.grid(row=0,column=0,sticky='w')
                Label(head,text='✧' if self.app._quiet_icons('spark',c['accent'],14) is None else '',
                      image=self.app._quiet_icons('spark',c['accent'],14),font=self._font(11),text_color=c['accent'],width=20).pack(side='left')
                Label(head,text='Friday',font=self._font(11,'bold'),text_color=c['fg_dim']).pack(side='left',padx=5)
                body=ctk.CTkFrame(row,fg_color='transparent');body.grid(row=1,column=0,sticky='ew',padx=(25,3))
                last=None
                # Plain prose remains unboxed, like Quiet Chat. Small
                # headings are parsed without a full HTML/Markdown stack.
                for paragraph in record['text'].split('\n\n'):
                    heading=paragraph.startswith('## ')
                    label=Label(body,text=paragraph[3:] if heading else paragraph,
                                font=self._font(14,'bold') if heading else self._font(13),
                                text_color=c['term_err'] if record.get('error') else
                                    c['fg_faint'] if record.get('pending') else c['fg'] if heading else c['fg_dim'],
                                height=1,wraplength=250,justify='left',anchor='w')
                    label.pack(fill='x',anchor='w',pady=(5,6));self._wrap_labels.append((label,'assistant'));last=label
                label=last
                if not record.get('pending'):
                    copy=Button(body,text='Copy',image=self.app._quiet_icons('copy',c['fg_faint'],11),
                                font=self._font(9),width=57,height=23,fg_color='transparent',
                                hover_color=c['input'],text_color=c['fg_faint'],corner_radius=6,
                                command=lambda t=record['text']:self.copy_message(t))
                    copy.pack(anchor='w',pady=(0,4))
            self._rows[record['id']]=(row,label)
            self._wrap();return row,label
        def _render_all(self):
            for child in self.log.winfo_children():child.destroy()
            self._rows={};self._wrap_labels=[];self._status_row=self._decision_row=None;self._status_labels={}
            self._steps_row=None;self._step_labels={};self._steps_head=None;self._steps_sub=None
            for index,record in enumerate(self.state.messages):
                result=self._render_message(record,index)
                if record['id']==self.state.pending_id:self._pending_bubble=result
            if self.state.steps or self.state.task_title:
                self._render_steps_card()
            if self.state.build:
                for phase,(status,detail) in self.state.build.copy().items():self.update_build_status(phase,status,detail)
            if self.state.options:self._show_decision_options(self.state.options.copy())
            self._scroll_end()
        def _wrap(self,event=None):
            if self._closed:return
            width=self.log._parent_canvas.winfo_width()/self._get_widget_scaling()
            if width<80:width=310
            valid=[]
            for label,role in self._wrap_labels:
                if label.winfo_exists():
                    label.configure(wraplength=max(100,width-(64 if role=='user' else 34)))
                    valid.append((label,role))
            self._wrap_labels=valid
        def _scroll_end(self):
            self.after_idle(lambda:self.log.see('end') if not self._closed else None)
        def _position_cards(self):
            nextrow=len(self.state.messages)+1
            if self._steps_row is not None:self._steps_row.grid_configure(row=nextrow);nextrow+=1
            if self._status_row is not None:self._status_row.grid_configure(row=nextrow);nextrow+=1
            if self._decision_row is not None:self._decision_row.grid_configure(row=nextrow)
        def _show_decision_options(self,options):
            self._dismiss_decision_options();self.state.options=list(options)[:4]
            c=core.C
            row=ctk.CTkFrame(self.log,fg_color=c['input'],corner_radius=13,border_width=1,border_color=c['border'])
            row.grid(row=len(self.state.messages)+2,column=0,sticky='ew',pady=(12,15));self._decision_row=row
            Label(row,text='YOUR CHOICE',font=self._font(10,'bold'),text_color=c['fg_dim'],anchor='w').pack(fill='x',padx=12,pady=(10,4))
            for option in self.state.options:
                Button(row,text=option,font=self._font(12),height=34,corner_radius=8,
                       fg_color=c['list_sel'],hover_color=c['hover'],text_color=c['fg'],anchor='w',
                       command=lambda o=option:self._choose_decision_option(o)).pack(fill='x',padx=10,pady=(5,0))
            custom=ctk.CTkFrame(row,fg_color='transparent');custom.pack(fill='x',padx=10,pady=11)
            entry=ctk.CTkEntry(custom,placeholder_text='Or type your own answer…',font=self._font(11),
                               fg_color=c['input'],text_color=c['fg'],border_color=c['border'],height=32,corner_radius=8)
            entry.pack(side='left',fill='x',expand=True)
            def submit(event=None):
                value=entry.get().strip()
                if value:self._choose_decision_option(value)
            entry.bind('<Return>',submit)
            icon_button(self.app,custom,'arrow_up',submit,'Send custom answer',size=14,width=28,height=30).pack(side='left',padx=(5,0))
            self._position_cards();self._scroll_end()
        def _dismiss_decision_options(self):
            self.state.options=[]
            if self._decision_row is not None:
                self._decision_row.destroy();self._decision_row=None
        def _choose_decision_option(self,text):self._dismiss_decision_options();self._submit_text(text)
        def update_build_status(self,phase,status,detail=''):
            if phase not in dict(self._STATUS_PHASE_LABELS):return
            self.state.build[phase]=(status,detail)
            c=core.C
            if self._status_row is None:
                row=ctk.CTkFrame(self.log,fg_color=c['input'],corner_radius=13,border_width=1,border_color=c['border'])
                row.grid(row=len(self.state.messages)+1,column=0,sticky='ew',pady=12);self._status_row=row
                Label(row,text='BUILD STATUS',font=self._font(10,'bold'),text_color=c['fg_dim'],anchor='w').pack(fill='x',padx=12,pady=(10,7))
                for key,name in self._STATUS_PHASE_LABELS:
                    label=Label(row,text='○  '+name,font=self._font(11),text_color=c['fg_faint'],
                                anchor='w',wraplength=250,justify='left')
                    label.pack(fill='x',padx=12,pady=(0,8));self._status_labels[key]=label
            name=dict(self._STATUS_PHASE_LABELS)[phase]
            glyph,color={'in_progress':('◷','fg'),'done':('✓','success'),'blocked':('!','term_err')}.get(status,('○','fg_faint'))
            self._status_labels[phase].configure(text=f'{glyph}  {name}'+(' — '+detail if detail else ''),text_color=c[color])
            self._position_cards();self._scroll_end()
        def _step_glyph(self,record,c):
            if record['kind']=='thought':return ('»',c['fg_faint'])
            return {'in_progress':('◷',c['fg']),'done':('✓',c['success']),
                    'blocked':('!',c['term_err'])}.get(record['status'],('○',c['fg_faint']))
        def _clear_steps_card(self):
            if self._steps_row is not None:
                try:self._steps_row.destroy()
                except tk.TclError:pass
            self._steps_row=None;self._step_labels={};self._steps_head=None;self._steps_sub=None
        def _steps_elapsed(self):
            if self.state.task_started is None:return ''
            seconds=time.monotonic()-self.state.task_started
            s=int(seconds)
            return f"{s//60}m {s%60:02d}s" if s>=60 else f"{s}s"
        def _steps_header_text(self):
            n=len(self.state.steps)
            if self.state.task_finished:
                return self.state.task_finished+' · '+str(n)+' step'+('s' if n!=1 else '')
            elapsed=self._steps_elapsed()
            return ('working'+((' · '+elapsed) if elapsed else '')+
                    ' · '+str(n)+' step'+('s' if n!=1 else ''))
        def update_task_step(self,kind,label,status='in_progress',detail='',step_id=None):
            """Upsert one row of the live task-steps card. Called (via the
            results queue) for every real action the agent takes while it
            works this task, so the thinking -> writing -> verifying ->
            debugging path stays visible to the user end to end."""
            if self._closed:return
            record=None
            if step_id is not None:
                record=next((s for s in self.state.steps if s.get('step_id')==step_id),None)
            if record is None:
                record={'step_id':step_id or uuid4().hex,'kind':kind,'label':label,
                        'status':status,'detail':detail}
                self.state.steps.append(record)
                order_only=True
            else:
                if detail:record['detail']=detail
                record.update(kind=kind,label=label,status=status)
                order_only=False
            c=core.C
            if self._steps_row is None:self._render_steps_card()
            elif len(self.state.steps)>26:self._render_steps_card()  # collapse oldest
            else:
                if self._steps_sub is not None and self._steps_sub.winfo_exists():
                    self._steps_sub.configure(text=self._steps_header_text())
                row=self._step_labels.get(record['step_id'])
                if row is None:
                    self._add_step_row(record)
                else:
                    glyph,color=self._step_glyph(record,c)
                    text=record['label']+((' — '+record['detail']) if record['detail'] else '')
                    row.configure(text=f'{glyph}  {text}',text_color=color)
            self._position_cards();self._scroll_end()
        def _add_step_row(self,record):
            c=core.C
            glyph,color=self._step_glyph(record,c)
            text=record['label']+((' — '+record['detail']) if record['detail'] else '')
            label=Label(self._steps_body,text=f'{glyph}  {text}',font=self._font(11),
                        text_color=color,anchor='w',wraplength=252,justify='left')
            label.pack(fill='x',padx=12,pady=(0,6))
            self._step_labels[record['step_id']]=label
        def _render_steps_card(self):
            self._clear_steps_card()
            c=core.C
            row=ctk.CTkFrame(self.log,fg_color=c['input'],corner_radius=13,
                             border_width=1,border_color=c['border'])
            row.grid(row=len(self.state.messages)+1,column=0,sticky='ew',pady=12)
            self._steps_row=row
            title=self.state.task_title or 'Working'
            self._steps_head=Label(row,text='TASK · '+title,font=self._font(10,'bold'),
                                   text_color=c['fg_dim'],anchor='w')
            self._steps_head.pack(fill='x',padx=12,pady=(10,2))
            self._steps_sub=Label(row,text=self._steps_header_text(),font=self._font(9),
                                  text_color=c['fg_faint'],anchor='w')
            self._steps_sub.pack(fill='x',padx=12,pady=(0,6))
            self._steps_body=ctk.CTkFrame(row,fg_color='transparent')
            self._steps_body.pack(fill='x')
            records=self.state.steps
            hidden=len(records)-26
            if hidden>0:
                Label(self._steps_body,text=f'···  {hidden} earlier steps',
                      font=self._font(10),text_color=c['fg_faint'],anchor='w').pack(fill='x',padx=12,pady=(0,6))
                records=records[-26:]
            for record in records:self._add_step_row(record)
        def _finish_task_steps(self,ok):
            started=self.state.task_started
            if started is None and not self.state.steps:return
            elapsed=self._steps_elapsed()
            n=len(self.state.steps)
            # Honesty rule: an Act Script review that came back red (a
            # blocked 'Review' verify row) means the task did NOT really
            # complete, even though the chat reply itself arrived fine --
            # the card must not claim "✓ done" next to a failed review.
            # (Rows arrive pre-numbered as "Step N — Review — …", so match
            # the Review label both bare and numbered.)
            review_blocked=any(r['status']=='blocked' and r['kind']=='verify'
                               and (str(r['label']).startswith('Review')
                                    or ' — Review —' in str(r['label']))
                               for r in self.state.steps)
            if ok and review_blocked:
                self.state.task_finished='✗ issues found'
            else:
                self.state.task_finished='✓ done' if ok else '! failed'
            self.state.task_finished+=(f' in {elapsed}' if elapsed else '')
            self.state.task_started=None
            # Any row still mid-flight counts as interrupted, not running.
            if not ok:
                for record in self.state.steps:
                    if record['status']=='in_progress':record['status']='blocked'
            if self._steps_row is not None:
                if self._steps_sub is not None and self._steps_sub.winfo_exists():
                    self._steps_sub.configure(text=self._steps_header_text(),
                        text_color=core.C['success'] if ok and not review_blocked else core.C['term_err'])
                if not ok:
                    for record in self.state.steps:
                        row=self._step_labels.get(record['step_id'])
                        if row is not None and row.winfo_exists():
                            glyph,color=self._step_glyph(record,core.C)
                            text=record['label']+((' — '+record['detail']) if record['detail'] else '')
                            row.configure(text=f'{glyph}  {text}',text_color=color)
        def receive_proactive_message(self,text,options=None):
            if self._closed or not text:return
            self._dismiss_decision_options();self._add_bubble('friday',text)
            if options:self._show_decision_options(options)
        def copy_message(self,text):self.clipboard_clear();self.clipboard_append(text);self.notice('Reply copied.')
        def export_transcript(self):
            path=filedialog.asksaveasfilename(parent=self.app,title='Export conversation',initialfile='Friday workspace chat.txt',
                                              defaultextension='.txt',filetypes=[('Text file','*.txt')])
            if path:
                try:
                    Path(path).write_text('\n\n'.join(('You' if m['role']=='you' else 'Friday')+'\n'+m['text'] for m in self.state.messages if not m['pending']),encoding='utf-8')
                    self.notice('Conversation exported.')
                except OSError:self.notice('Couldn’t write that file. Choose another location.')
        # -- one-shot voice input ------------------------------------------
        def _toggle_mic(self):
            from . import chat_voice
            if self._mic_rec is not None and self._mic_rec.recording:
                rec=self._mic_rec
                # second click: stop and transcribe into the composer
                self.notice('… transcribing')
                rec.stop(lambda text,err,engine:self._mic_q.put((text,err)))
                return
            ok,reason=chat_voice.availability()
            if not ok:
                self.notice(f"Voice input isn't ready on this machine: {reason}")
                return
            if self._mic_rec is None:
                self._mic_rec=chat_voice.ChatVoiceRecorder()
            rec=self._mic_rec
            started,err=rec.start()
            if not started:
                self.notice(f'The microphone did not start: {err}')
                return
            self.mic_button.configure(fg_color='#3B82F6')
            self.notice(f'Recording ({chat_voice.engine_label()}) -- click the mic again to stop.')

        def _on_mic_transcript(self,text,err):
            self.mic_button.configure(fg_color='transparent')
            if err:
                self.notice(f'I could not hear that properly: {err}')
                return
            # The confirmable flow: transcript lands EDITABLE in the
            # composer -- review, optionally fix, then Enter sends it
            # through the exact same _send path as typed text.
            self._showing_placeholder=False
            self.entry.delete(0,'end')
            self.entry.insert(0,text)
            self._update_composer()
            self.entry.focus_set()
            self.notice('Transcribed -- review and press Enter to send.')

        def notice(self,text):
            self._last_notice=text;self.footer.configure(text=text)
            self.after(3500,lambda:self.footer.configure(text='Enter to send · Shift + Enter for a new line'))
        def destroy(self):
            if self._closed:return
            self.state.draft=self.entry.get();self._closed=True
            super().destroy()
    return ChatSidePanel
