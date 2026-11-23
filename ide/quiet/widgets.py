"""Owner-safe CTk controls and small compatibility adapters for Friday APIs."""
import tkinter as tk
import customtkinter as ctk
from friday_chat.widgets import _OwnedTimers, PanelButton, PanelTextbox, PanelToplevel, PanelScrollFrame
from .icons import ALIASES, FALLBACKS
from .tokens import ui_font


class Label(ctk.CTkLabel):
    def event_generate(self,sequence,**kwargs):
        if sequence.startswith('<Button'):
            return self._label.event_generate(sequence,**kwargs)
        return super().event_generate(sequence,**kwargs)
    # Existing terminal/status/media code uses a few native style names.
    def configure(self, require_redraw=False, **kwargs):
        if 'fg' in kwargs:kwargs['text_color']=kwargs.pop('fg')
        if 'bg' in kwargs:kwargs['fg_color']=kwargs.pop('bg')
        return super().configure(require_redraw=require_redraw,**kwargs)
    def cget(self,name):
        return super().cget({'fg':'text_color','bg':'fg_color'}.get(name,name))


class Button(PanelButton):
    def event_generate(self,sequence,**kwargs):
        if sequence in {'<Button-1>','<ButtonPress-1>','<ButtonRelease-1>'}:
            return self._canvas.event_generate(sequence,**kwargs)
        return super().event_generate(sequence,**kwargs)
    def configure(self,require_redraw=False,**kwargs):
        if 'bg' in kwargs:kwargs['fg_color']=kwargs.pop('bg')
        if 'fg' in kwargs:kwargs['text_color']=kwargs.pop('fg')
        return super().configure(require_redraw=require_redraw,**kwargs)
    def cget(self,name):
        return super().cget({'fg':'text_color','bg':'fg_color'}.get(name,name))


class Entry(ctk.CTkEntry):
    def selection_range(self,start,end):return self._entry.selection_range(start,end)
    def selection_clear(self):return self._entry.selection_clear()


class Composer(PanelTextbox):
    """Multiline textbox, retaining the Entry-shaped API used by Friday.

    Native Text access is available as `_textbox`. Existing integrations
    can still get(), insert(0,...), delete(0,'end') and focus_set().
    """
    @staticmethod
    def _index(index):
        if isinstance(index,int):return '1.0' if index==0 else '1.0+%dc'%index
        return index
    def get(self,index1=None,index2=None):
        if index1 is None:return super().get('1.0','end-1c')
        return super().get(self._index(index1),self._index(index2) if index2 is not None else None)
    def delete(self,index1,index2=None):
        return super().delete(self._index(index1),self._index(index2) if index2 is not None else None)
    def insert(self,index,text,tags=None):
        return super().insert(self._index(index),text,tags)
    def icursor(self,index):self._textbox.mark_set('insert',self._index(index))
    def selection_range(self,start,end):self._textbox.tag_add('sel',self._index(start),self._index(end))
    def event_generate(self,sequence,**kwargs):return self._textbox.event_generate(sequence,**kwargs)


class Tooltip:
    def __init__(self,widget,text,app):
        self.widget,self.text,self.app=widget,text,app
        self.job=self.window=None
        for sequence,fn in [('<Enter>',self.schedule),('<Leave>',self.hide),
                            ('<ButtonPress>',self.hide),('<Destroy>',self.hide)]:
            widget.bind(sequence,fn,add='+')
    def schedule(self,event=None):
        self.hide();self.job=self.widget.after(650,self.show)
    def show(self):
        self.job=None
        if not self.widget.winfo_exists():return
        c=self.app._quiet_core.C
        self.window=PanelToplevel(self.widget)
        self.window.overrideredirect(True);self.window.configure(fg_color=c['panel'])
        self.window.attributes('-topmost',True)
        Label(self.window,text=self.text,font=ui_font(self.app,11),
              text_color=c['fg'],fg_color=c['panel'],corner_radius=8).pack(padx=10,pady=6)
        self.window.geometry('+%d+%d'%(self.widget.winfo_rootx(),
             self.widget.winfo_rooty()+self.widget.winfo_height()+5))
    def hide(self,event=None):
        if self.job:
            try:self.widget.after_cancel(self.job)
            except tk.TclError:pass
            self.job=None
        if self.window:
            try:self.window.destroy()
            except tk.TclError:pass
            self.window=None


def icon_button(app,parent,name,command,tip='',size=17,width=32,height=32,background='transparent'):
    c=app._quiet_core.C
    image=app._quiet_icons(name,c['fg_dim'],size)
    glyph='' if image is not None else FALLBACKS.get(ALIASES.get(name,name),'•')
    button=Button(parent,text=glyph,image=image,command=command,font=ui_font(app,12),
                  width=width,height=height,corner_radius=9,fg_color=background,
                  hover_color=c['hover'],text_color=c['fg_dim'])
    if tip:app.tooltip(button,tip)
    return button


def action_button(app,parent,text,command,primary=False,width=100,height=34,icon=None):
    c=app._quiet_core.C
    return Button(parent,text=text,command=command,
                  image=app._quiet_icons(icon,c['fg_dim'],15) if icon else None,
                  font=ui_font(app,12),height=height,width=width,corner_radius=9,
                  border_width=1,border_color=c['border'],
                  fg_color=c['list_sel'] if primary else c['input'],
                  hover_color=c['hover'],text_color=c['fg'])
