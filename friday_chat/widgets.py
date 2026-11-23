"""Small lifecycle adaptations for CustomTkinter inside Friday's Tk host."""
import tkinter as tk
import customtkinter as ctk


class PanelScrollFrame(ctk.CTkScrollableFrame):
    """Keep scrolling local to this native panel and clean up its bindings.

    CTkScrollableFrame 5.2 registers wheel/Shift callbacks with bind_all,
    on the hidden Tk root. Those callbacks outlive a destroyed child view
    and can retain it after repeatedly closing/reopening Friday's panels.
    A Toplevel binding sees its own descendants' events without touching
    the IDE, other panels, or any existing root/global key bindings.
    """
    def __init__(self, *args, **kwargs):
        self._panel_scroll_bindings = []
        super().__init__(*args, **kwargs)
        # No visible side scrollbar anywhere: every panel scrolls with
        # the mouse wheel / two-finger touchpad. The scrollbar WIDGET
        # stays constructed (CTk keeps wiring thumb colors against it)
        # but never enters the layout.
        try:
            self._scrollbar.grid_remove()
        except (AttributeError, tk.TclError):
            pass
        top = self.winfo_toplevel()
        for sequence in ("<Button-4>", "<Button-5>"):
            identifier = tk.Misc.bind(top, sequence, self._linux_wheel, add="+")
            self._panel_scroll_bindings.append((top, sequence, identifier))

    def bind_all(self, sequence=None, func=None, add=None):
        top = self.winfo_toplevel()
        identifier = tk.Misc.bind(top, sequence, func, add="+")
        self._panel_scroll_bindings.append((top, sequence, identifier))
        return identifier

    def _linux_wheel(self, event):
        if self._check_if_master_is_canvas(event.widget):
            direction = -3 if event.num == 4 else 3
            horizontal = self._orientation == "horizontal" or self._shift_pressed
            if horizontal:
                self._parent_canvas.xview_scroll(direction, "units")
            else:
                self._parent_canvas.yview_scroll(direction, "units")

    def destroy(self):
        for top, sequence, identifier in self._panel_scroll_bindings:
            try:
                tk.Misc.unbind(top, sequence, identifier)
            except tk.TclError:
                pass
        self._panel_scroll_bindings.clear()
        super().destroy()


class _OwnedTimers:
    """Cancel all widget-owned callbacks before Tk deletes their commands.

    Stock CTkTextbox 5.2 keeps an untracked 200ms scrollbar-check loop,
    CTkButton schedules a click animation, and CTkToplevel queues setup
    callbacks. Closing a native panel must not leave any of these in
    Friday's long-lived hidden interpreter as invalid Tcl commands.
    """
    def __init__(self, *args, **kwargs):
        self._owned_after_jobs = set()
        self._timer_widget_closed = False
        super().__init__(*args, **kwargs)

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        identifier = None

        def run():
            self._owned_after_jobs.discard(identifier)
            if not self._timer_widget_closed:
                func(*args)

        identifier = super().after(ms, run)
        self._owned_after_jobs.add(identifier)
        return identifier

    def after_cancel(self, identifier):
        self._owned_after_jobs.discard(identifier)
        return super().after_cancel(identifier)

    def destroy(self):
        if self._timer_widget_closed:
            return
        self._timer_widget_closed = True
        for identifier in list(self._owned_after_jobs):
            try:
                self.after_cancel(identifier)
            except tk.TclError:
                pass
        super().destroy()


class PanelTextbox(_OwnedTimers, ctk.CTkTextbox):
    pass


class PanelButton(_OwnedTimers, ctk.CTkButton):
    def _clicked(self, event=None):
        result = super()._clicked(event)
        # Tk snapshots bindtags for a mouse event before dispatch. If
        # the command closes its window, bubbling to that Toplevel
        # would invoke a just-deleted dismiss-menu callback.
        return "break" if self._timer_widget_closed else result


class PanelToplevel(_OwnedTimers, ctk.CTkToplevel):
    pass
