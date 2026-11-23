"""Quiet Chat's palette translated into Friday's existing semantic tokens."""


def install_palette(core):
    # Keep every original theme/token/API, including black/white. Only
    # the default grey presentation is redesigned; syntax retains meaning.
    core.THEMES['grey'].update({
        'text_fg': '#D8D7DA', 'caret': '#C7C3BC', 'sel_inactive': '#34343B',
        'term_cwd': '#B2B7AA', 'term_err': '#D39B9E', 'term_info': '#B2B1B8',
        'editor': '#151517', 'sidebar': '#1B1B1E', 'activity': '#151517',
        'menubar': '#151517', 'menubar_hover': '#303035', 'panel': '#1B1B1E',
        'chat': '#151517', 'terminal': '#18181B', 'statusbar': '#151517',
        'status_fg': '#A6A5AE', 'success': '#A9BDA6', 'warn': '#C4B598',
        'danger': '#D39B9E', 'fg': '#E7E6E3', 'fg_dim': '#B2B1B8',
        'fg_faint': '#909098', 'fg_bright': '#F0EFEC', 'border': '#35353B',
        'border_soft': '#29292E', 'border_hi': '#29292E', 'tabbar': '#151517',
        'tab_active': '#28282D', 'tab_inactive': '#151517',
        'tab_in_fg': '#A1A0A9', 'hover': '#303035', 'list_sel': '#2A2A2F',
        'input': '#1E1E21', 'accent': '#C7C3BC', 'accent_btn': '#29292E',
        'accent_hover': '#37373D', 'link': '#C6C6D0', 'scroll': '#35353B',
        'scroll_hover': '#53535B', 'cur_line': '#1D1D21', 'sel': '#3A3A43',
        'gutter': '#787780', 'gutter_cur': '#C7C3BC', 'kw': '#ABB9CB',
        'str': '#C9B59C', 'com': '#8F9A8D', 'num': '#B6BBA8',
        'fn': '#CDC6AE', 'cls': '#A5B8B2', 'dec': '#BEB2C5',
        'findall': '#404048', 'findcur': '#66616A',
    })
    # Native Tk reports lower-case colour values; retain Friday’s
    # canonical token representation for public/status comparisons.
    for palette in core.THEMES.values():
        for key,value in palette.items():palette[key]=value.lower()
    core.CAT_COLOR['folder'] = '#aaa8a4'
    core.C.clear()
    core.C.update(core.THEMES[core.DEFAULT_THEME])
    core.TAG_COLOR.clear()
    core.TAG_COLOR.update(core._tag_colors())
    core._refresh_guide_color()


def ui_font(app, size=12, weight='normal'):
    family = getattr(app, 'ui_font_family', None) or getattr(app, 'f_ui', ('Arial',))[0]
    # Explicit tuples: never allocate a CTkFont on a default Tk root.
    return (family, size, weight)
