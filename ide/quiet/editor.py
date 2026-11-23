"""Quiet tab/search/welcome chrome around Friday's unmodified native editor."""
import tkinter as tk
import customtkinter as ctk
from .tokens import ui_font
from .widgets import _OwnedTimers, Label, Button, Entry, PanelScrollFrame, icon_button, action_button


def make_editor(core):
    class EditorArea(_OwnedTimers,core.EditorArea):
        def __init__(self,app):
            self._quiet_scroll_ready=False
            super().__init__(app)
            c=core.C
            self.tabbar.destroy();self.crumb.destroy()
            self.tabbar=ctk.CTkFrame(self,fg_color=c['tabbar'],height=49,corner_radius=0)
            self.tabbar.pack(fill='x',before=self.code_row);self.tabbar.pack_propagate(False)
            plus=icon_button(app,self.tabbar,'plus',app.new_file,'New file · Ctrl+N',size=15,width=29,height=29)
            plus.pack(side='right',padx=(2,12),pady=10)
            self.tabs_row=PanelScrollFrame(self.tabbar,orientation='horizontal',height=32,
                                          fg_color='transparent',corner_radius=0,
                                          label_font=ui_font(app,11),scrollbar_button_color=c['tabbar'],
                                          scrollbar_button_hover_color=c['border'])
            self.tabs_row.pack(side='left',fill='both',expand=True,padx=(12,0),pady=(7,0))
            self.tabs_row.bind('<Double-1>',lambda e:app.new_file())
            self.crumb=Label(self,text='',font=ui_font(app,10),text_color=c['fg_faint'],
                             fg_color=c['editor'],anchor='w',height=27)
            self.crumb.pack(fill='x',padx=21,before=self.code_row)
            self.vbar.destroy();self.hbar.destroy()
            self.vbar=ctk.CTkScrollbar(self.code_row,orientation='vertical',width=8,
                                      fg_color=c['editor'],button_color=c['border'],
                                      button_hover_color=c['scroll_hover'],border_spacing=1,
                                      command=self._yview)
            # Side scrollbar removed: the text scrolls natively on
            # wheel/touchpad; bottom horizontal bar stays.
            # (vbar still receives _on_scroll updates, never packed.)
            self.hbar=ctk.CTkScrollbar(self,orientation='horizontal',height=7,
                                      fg_color=c['editor'],button_color=c['border'],
                                      button_hover_color=c['scroll_hover'],border_spacing=1,
                                      command=self.text.xview)
            self.hbar.pack(fill='x')
            self._quiet_scroll_ready=True
            self.text.configure(padx=9,pady=12,insertwidth=1)
            self.empty_label.destroy()
            self.empty.configure(bg=c['editor'])
            hero=ctk.CTkFrame(self.empty,fg_color='transparent')
            hero.place(relx=.5,rely=.43,anchor='center')
            mark=ctk.CTkFrame(hero,width=65,height=65,corner_radius=20,
                              fg_color=c['panel'],border_color=c['border'],border_width=1)
            mark.pack(pady=(0,21));mark.pack_propagate(False)
            Label(mark,text='✧' if app._quiet_icons('spark',c['accent'],29) is None else '',
                  image=app._quiet_icons('spark',c['accent'],29),font=ui_font(app,18),text_color=c['accent']).place(relx=.5,rely=.5,anchor='center')
            Label(hero,text='A quieter place to build.',font=ui_font(app,26,'bold'),text_color=c['fg']).pack(pady=(0,10))
            Label(hero,text='Your files, your ideas, a little room to focus.',font=ui_font(app,13),text_color=c['fg_dim']).pack(pady=(0,22))
            row=ctk.CTkFrame(hero,fg_color='transparent');row.pack()
            action_button(app,row,'Open folder',app.open_folder_dialog,icon='folder').pack(side='left',padx=5)
            action_button(app,row,'New file',app.new_file,icon='plus').pack(side='left',padx=5)
            self.empty_label=Label(hero,text=core.EMPTY_HINT,font=ui_font(app,11),text_color=c['fg_faint'],justify='left')
            self.empty_label.pack(pady=(26,0))
            # Native Text/gutter/minimap keep the original tags, undo,
            # large-file lexer and scroll/navigation behaviour intact.
        def _on_scroll(self,first,last):
            core._OriginalEditorArea._on_scroll(self,first,last)
            if getattr(self,'_quiet_scroll_ready',False):
                needed=float(last)-float(first)<.999
                self.vbar.configure(button_color=core.C['border'] if needed else core.C['editor'])
        def _on_xscroll(self,*args):
            core._OriginalEditorArea._on_xscroll(self,*args)
            if getattr(self,'_quiet_scroll_ready',False) and len(args)==2:
                needed=float(args[1])-float(args[0])<.999
                self.hbar.configure(button_color=core.C['border'] if needed else core.C['editor'])
        def _make_tab(self,index,buffer):
            app=self.app;c=core.C;active=index==app.current
            frame=ctk.CTkFrame(self.tabs_row,fg_color=c['tab_active'] if active else 'transparent',
                               corner_radius=9,border_width=1 if active else 0,border_color=c['border'])
            frame.pack(side='left',padx=(0,6),pady=(0,5))
            name=Label(frame,text=buffer['name'],image=app._quiet_icons('file',c['fg_dim'],13),
                       compound='left',font=ui_font(app,12),text_color=c['fg'] if active else c['tab_in_fg'],anchor='w',height=30)
            name.pack(side='left',padx=(12,8))
            if buffer.get('modified'):
                Label(frame,text='•',font=ui_font(app,13),width=10,text_color=c['accent']).pack(side='left',padx=(0,4))
            close=icon_button(app,frame,'close',lambda i=index:app.close_tab(i),
                              'Close '+buffer['name'],size=12,width=23,height=24)
            close.pack(side='left',padx=(0,5),pady=4)
            for widget in [frame,name]:
                widget.bind('<Button-1>',lambda e,i=index:app.switch_tab(i))
                widget.bind('<Button-2>',lambda e,i=index:app.close_tab(i))
                widget.bind('<Button-3>',lambda e,i=index:app.tab_menu(e,i))
        def _build_findbar(self):
            app=self.app;c=core.C;f=self.findbar
            # This method is dispatched during the original constructor.
            # All field names/Entry-shaped operations stay tool-compatible.
            self.fe=Entry(f,width=200,height=32,font=ui_font(app,12),
                                fg_color=c['input'],text_color=c['fg'],border_color=c['border'],corner_radius=8)
            self.fe.pack(side='left',padx=(9,2),pady=8)
            self.fcount=Label(f,text='',font=ui_font(app,10),text_color=c['fg_dim'],width=68)
            self.fcount.pack(side='left')
            for name,cmd in [('chevron-up',lambda:self.find_next(-1)),('chevron-down',lambda:self.find_next(1)),('close',self.hide_find)]:
                icon_button(app,f,name,cmd,name.replace('-',' ').title(),size=13,width=25,height=27).pack(side='left',padx=1)
            self.fe.bind('<KeyRelease>',lambda e:self.do_find());self.fe.bind('<Return>',lambda e:self.find_next(1))
            self.fe.bind('<Shift-Return>',lambda e:self.find_next(-1));self.fe.bind('<Escape>',lambda e:self.hide_find())
            self.re_bar=ctk.CTkFrame(f,fg_color=c['sidebar'],corner_radius=0)
            self.re=Entry(self.re_bar,width=200,height=32,font=ui_font(app,12),
                                fg_color=c['input'],text_color=c['fg'],border_color=c['border'],corner_radius=8)
            self.re.pack(side='left',padx=(9,4),pady=(0,8))
            for text,cmd in [('Replace',self.replace_one),('Replace All',self.replace_all)]:
                action_button(app,self.re_bar,text,cmd,width=80,height=30).pack(side='left',padx=2,pady=(0,8))
            self.re.bind('<Escape>',lambda e:self.hide_find())
    return EditorArea
