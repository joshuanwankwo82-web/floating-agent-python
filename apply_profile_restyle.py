#!/usr/bin/env python3
"""Settings panel: professional Profile page, photo on top, Profile first.

Run from the floating-agent-python folder:

    python apply_profile_restyle.py            # apply
    python apply_profile_restyle.py --dry-run  # only report what would change
    python apply_profile_restyle.py --undo     # restore the .bak-profile backups

What changes (settings_panel.py)
  * The top switcher reads  [ Profile | Keys & Connect ]  and Settings now
    OPENS ON PROFILE (pass initial_page="keys" to open on keys instead).
  * Profile page redesign: a header card at the very top with a large round
    photo (click it to upload), your name, handles and a completeness
    meter; every section (About you, Links, Emails, Connect apps, GitHub
    authors, Web passwords) sits in its own rounded card with two-column
    fields; "Save profile" is pinned to the bottom so it is always visible.
  * The hint line under the title describes whichever page is showing.
  Nothing about what is SAVED changes -- same fields, same encrypted store.

Works on edited copies: methods are found BY NAME with Python's parser, not
by line numbers. If your Profile page has fields this redesign doesn't
know about, the script stops WITHOUT changing anything and tells you which
-- so nothing you added locally can be dropped.
"""
import ast
import os
import py_compile
import re
import shutil
import sys
import textwrap

HERE = os.getcwd()
BAK = ".bak-profile"
MARK = "def _retitle_subheader(self, name):"
NEW_METHODS = '    def show_page(self, name):\n        """Flip the top switcher. Both pages are always built (cheap, keeps\n        every row\'s state while you hop page to page); only the packing\n        changes. `initial_page` lets the \'open my profile\' voice route\n        land here directly (see app_panels\' profile redirect). Profile is\n        the first tab and the page Settings opens on."""\n        self._page = name\n        # keep the top switcher\'s active segment honest even when a page\n        # flip came from outside (e.g. the \'open my profile\' redirect)\n        self._seg.set(self._page_labels[name])\n        if name == "keys":\n            self._check_all_btn.pack(side="right", padx=8, pady=6)\n        else:\n            self._check_all_btn.pack_forget()\n        self._retitle_subheader(name)\n        hosts = {"keys": self._keys_host, "profile": self._profile_host}\n        for key, host in hosts.items():\n            if key == name:\n                host.pack(side="top", fill="both", expand=True)\n            else:\n                host.pack_forget()\n\n    def _retitle_subheader(self, name):\n        """The one-line hint under the title bar describes the page that is\n        showing (its original text is about API keys, which read oddly\n        above the Profile page now that Profile opens first)."""\n        try:\n            lbl = getattr(self, "_subheader_lbl", None)\n            if lbl is None:\n                # the hint label is the first plain label in the "Check\n                # all" button\'s row (the scroll probe is the other one)\n                for child in self._check_all_btn.master.winfo_children():\n                    if (isinstance(child, ctk.CTkLabel)\n                            and child is not getattr(self, "_scroll_probe", None)):\n                        lbl = child\n                        break\n                if lbl is None:\n                    return\n                self._subheader_lbl = lbl\n                self._subheader_keys_text = lbl.cget("text")\n            if name == "profile":\n                lbl.configure(text=(\n                    "Your profile \\u2014 Friday reads it to know who you "\n                    "are. Passwords and tokens are encrypted and never "\n                    "reach a prompt."))\n            else:\n                lbl.configure(text=self._subheader_keys_text)\n        except Exception:  # noqa: BLE001 -- cosmetic only\n            pass\n\n    def _build_profile_page(self, parent):\n        """Who the user is: identity, links, photo, bio, connected apps,\n        and (encrypted + always-masked) web passwords -- everything the\n        user chose to keep here in their own words. Saved blob lands in\n        Friday\'s encrypted store; Friday reads it into every prompt.\n\n        Layout: a profile header card (large round photo, name, handles,\n        completeness meter) on top, then one rounded card per section,\n        and a Save bar pinned to the bottom so it is always reachable."""\n        bg = C["panel"]          # page background\n        card_bg = C["editor"]    # cards sit one step "up" from the page\n        line = C.get("border_soft", C["border"])\n\n        def _on_accent():\n            """Readable text colour on the accent (light accents get dark\n            text instead of white-on-pale)."""\n            try:\n                r, g, b = (int(C["accent"][i:i + 2], 16) for i in (1, 3, 5))\n                return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 \\\n                    else "#ffffff"\n            except Exception:  # noqa: BLE001\n                return "#ffffff"\n        on_accent = _on_accent()\n\n        page = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0)\n\n        # -- pinned Save bar (outside the scroll area) --\n        footer = ctk.CTkFrame(page, fg_color=bg, corner_radius=0)\n        footer.pack(side="bottom", fill="x")\n        ctk.CTkFrame(footer, fg_color=line, height=1,\n                     corner_radius=0).pack(side="top", fill="x")\n        save_row = ctk.CTkFrame(footer, fg_color="transparent")\n        save_row.pack(fill="x", padx=18, pady=10)\n        panel_ui.style_button(\n            ctk.CTkButton(save_row, text="Save profile", width=132,\n                          height=34, command=self._save_profile),\n            C, "primary").pack(side="right")\n        save_row.winfo_children()[-1].configure(text_color=on_accent)\n        self._profile_status = ctk.CTkLabel(\n            save_row, text="Changes are kept only after you press Save.",\n            text_color=C["fg_dim"], font=("Segoe UI", 10), anchor="w")\n        self._profile_status.pack(side="left", fill="x", expand=True)\n\n        # -- scroll area --\n        host = ctk.CTkFrame(page, fg_color=bg, corner_radius=0)\n        host.pack(side="top", fill="both", expand=True)\n        canvas = tk.Canvas(host, bg=bg, highlightthickness=0, bd=0)\n        canvas.pack(side="left", fill="both", expand=True)\n        body = ctk.CTkFrame(canvas, fg_color=bg, corner_radius=0)\n        win = canvas.create_window((0, 0), window=body, anchor="nw")\n\n        def _body_conf(_e):\n            # scrollregion anchored at x=0: a region starting at the\n            # centered column\'s x would scroll the column flush-left\n            box_ = canvas.bbox("all")\n            if box_:\n                canvas.configure(scrollregion=(0, 0, box_[2], box_[3]))\n\n        def _canvas_conf(e):\n            # Same panel-fitting column as the keys page (F36): nothing\n            # clips; the right-anchored actions guarantee that.\n            w = min(e.width - 2 * _CONTENT_PAD, _CONTENT_CAP)\n            # canvas-window width (not body.configure) -- pack doesn\'t\n            # clamp childrens\' natural widths.\n            canvas.itemconfigure(win, width=w)\n            canvas.coords(win, max(0, (e.width - w) // 2), 0)\n\n        body.bind("<Configure>", _body_conf)\n        canvas.bind("<Configure>", _canvas_conf)\n        touchpad_scroll.attach_edge_autoscroll(self, canvas, tick_ms=20)\n        self._profile_canvas = canvas\n        self._add_jump_buttons(host, canvas, "profile")\n\n        import user_profile\n        profile = user_profile.load()\n        self._profile_vars = {}\n        self._photo_img = None\n\n        # ---------- small layout helpers ----------\n        def _heading(text):\n            """One rounded section card; returns its content frame."""\n            card = ctk.CTkFrame(body, fg_color=card_bg, corner_radius=12,\n                                border_width=1, border_color=line)\n            card.pack(fill="x", padx=14, pady=(12, 0))\n            ctk.CTkLabel(card, text=text, text_color=C["fg"], anchor="w",\n                         font=("Segoe UI", 13, "bold")).pack(\n                fill="x", padx=18, pady=(14, 0))\n            inner = ctk.CTkFrame(card, fg_color="transparent")\n            inner.pack(fill="x", padx=18, pady=(6, 16))\n            return inner\n\n        def _note(box, text):\n            """Dim helper text that re-wraps to the card\'s width."""\n            lbl = ctk.CTkLabel(box, text=text, text_color=C["fg_dim"],\n                               font=("Segoe UI", 10), justify="left",\n                               anchor="w", wraplength=560)\n            lbl.pack(fill="x", pady=(0, 6))\n            box.bind("<Configure>", lambda e: lbl.configure(\n                wraplength=max(200, e.width - 8)), add="+")\n            return lbl\n\n        def _grid2(box):\n            grid = ctk.CTkFrame(box, fg_color="transparent")\n            grid.pack(fill="x")\n            grid.grid_columnconfigure((0, 1), weight=1, uniform="col")\n            return grid\n\n        def _field(grid, row, col, label, placeholder="", secret=False,\n                   span=1):\n            cell = ctk.CTkFrame(grid, fg_color="transparent")\n            cell.grid(row=row, column=col, columnspan=span, sticky="ew",\n                      padx=(0 if col == 0 else 6, 6 if col + span < 2 else 0),\n                      pady=(6, 0))\n            ctk.CTkLabel(cell, text=label, text_color=C["fg_dim"],\n                         anchor="w", font=("Segoe UI", 10, "bold")).pack(\n                fill="x")\n            ent = _style_entry(ctk.CTkEntry(\n                cell, height=34, placeholder_text=placeholder,\n                show="\\u2022" if secret else ""))\n            ent.pack(fill="x", pady=(3, 0))\n            return ent\n\n        def _entry(grid, row, col, key, label, placeholder="", span=1):\n            ent = _field(grid, row, col, label, placeholder, span=span)\n            if profile.get(key):\n                ent.insert(0, profile[key])\n            self._profile_vars[key] = ent\n            return ent\n\n        # ================= header card: photo + identity =================\n        head = ctk.CTkFrame(body, fg_color=card_bg, corner_radius=14,\n                            border_width=1, border_color=line)\n        head.pack(fill="x", padx=14, pady=(14, 0))\n        try:\n            scale = float(ctk.ScalingTracker.get_widget_scaling(head))\n        except Exception:  # noqa: BLE001\n            scale = 1.0\n        av = int(round(112 * scale))            # avatar canvas size\n        photo_px = int(round(98 * scale))       # photo inside the ring\n\n        top = ctk.CTkFrame(head, fg_color="transparent")\n        top.pack(fill="x", padx=20, pady=(20, 12))\n        self._avatar_canvas = tk.Canvas(top, width=av, height=av,\n                                        bg=card_bg, highlightthickness=0,\n                                        bd=0, cursor="hand2")\n        self._avatar_canvas.pack(side="left", anchor="n")\n        ident = ctk.CTkFrame(top, fg_color="transparent")\n        ident.pack(side="left", fill="both", expand=True, padx=(18, 0))\n        self._hdr_name = ctk.CTkLabel(ident, text="", anchor="w",\n                                      text_color=C["fg"],\n                                      font=("Segoe UI", 20, "bold"))\n        self._hdr_name.pack(fill="x", pady=(6, 0))\n        self._hdr_handles = ctk.CTkLabel(ident, text="", anchor="w",\n                                         text_color=C["accent"],\n                                         font=("Segoe UI", 11))\n        self._hdr_handles.pack(fill="x")\n        self._hdr_meter_lbl = ctk.CTkLabel(ident, text="", anchor="w",\n                                           text_color=C["fg_dim"],\n                                           font=("Segoe UI", 10))\n        self._hdr_meter_lbl.pack(fill="x", pady=(8, 0))\n        self._hdr_meter = ctk.CTkProgressBar(\n            ident, height=6, corner_radius=3, fg_color=C["hover"],\n            progress_color=C["accent"])\n        self._hdr_meter.pack(fill="x", pady=(3, 0))\n\n        btns = ctk.CTkFrame(ident, fg_color="transparent")\n        btns.pack(fill="x", pady=(12, 0))\n\n        # photo source: file path or URL (what _save_profile stores)\n        src = ctk.CTkFrame(head, fg_color="transparent")\n        src.pack(fill="x", padx=20, pady=(0, 18))\n        self._photo_entry = _style_entry(ctk.CTkEntry(\n            src, height=32,\n            placeholder_text="\\u2026or paste an image path or https:// URL"))\n        self._photo_entry.pack(side="left", fill="x", expand=True)\n        if profile.get("photo"):\n            self._photo_entry.insert(0, profile["photo"])\n\n        def _draw_avatar():\n            cv = self._avatar_canvas\n            cv.delete("all")\n            m = av / 2\n            cv.create_oval(2, 2, av - 2, av - 2, outline=C["accent"],\n                           width=max(2, int(3 * scale)))\n            if self._photo_img is not None:\n                cv.create_image(m, m, image=self._photo_img)\n            else:\n                pad = int(8 * scale)\n                cv.create_oval(pad, pad, av - pad, av - pad,\n                               fill=C["hover"], outline="")\n                words = (self._profile_vars["name"].get().split()\n                         if "name" in self._profile_vars else [])\n                initials = "".join(w[0] for w in words[:2]).upper()\n                cv.create_text(m, m, text=initials or "+",\n                               fill=C["accent"],\n                               font=("Segoe UI", int(-34 * scale), "bold"))\n            # small "edit" badge, bottom-right: the photo is clickable\n            r, cx = 14 * scale, av - 16 * scale\n            cv.create_oval(cx - r, cx - r, cx + r, cx + r,\n                           fill=C["accent"], outline=card_bg,\n                           width=max(2, int(3 * scale)))\n            cv.create_text(cx, cx, text="\\u270e", fill=on_accent,\n                           font=("Segoe UI Symbol", int(-14 * scale)))\n\n        def _refresh_header(_e=None):\n            name = self._profile_vars["name"].get().strip()\n            handles = [h.strip().lstrip("@") for h in\n                       self._profile_vars["dev_names"].get().split(",")\n                       if h.strip()]\n            self._hdr_name.configure(text=name or "Your name")\n            self._hdr_name.configure(\n                text_color=C["fg"] if name else C["fg_faint"])\n            self._hdr_handles.configure(\n                text="   ".join("@" + h for h in handles[:4])\n                or "Add your handles below")\n            filled = [\n                bool(name), bool(handles),\n                bool(self._bio_text.get("1.0", "end").strip()),\n                bool(self._profile_vars["interests"].get().strip()),\n                bool(self._profile_vars["goals"].get().strip()),\n                bool(self._photo_entry.get().strip()),\n                any(e.get().strip() for e in self._link_vars.values()),\n            ]\n            done = sum(filled)\n            self._hdr_meter.set(done / len(filled))\n            self._hdr_meter_lbl.configure(\n                text=("Profile complete" if done == len(filled) else\n                      f"Profile {round(100 * done / len(filled))}% "\n                      "complete"))\n            if self._photo_img is None:\n                _draw_avatar()\n\n        self._refresh_profile_header = _refresh_header\n\n        def _preview(_e=None):\n            source = self._photo_entry.get().strip()\n            if not source:\n                self._clear_photo()\n                return\n            img = user_profile.avatar_photoimage(\n                {"photo": source}, size=photo_px, master=self)\n            if img is not None:\n                self._photo_img = img   # keep a reference: GC eats images\n                self._profile_status.configure(\n                    text="Photo updated \\u2014 press Save profile to keep it.",\n                    text_color=C["fg_dim"])\n            else:\n                self._photo_img = None\n                self._profile_status.configure(\n                    text="Couldn\'t load that photo \\u2014 check the path "\n                         "or URL.", text_color=C["danger"])\n            _draw_avatar()\n            _refresh_header()\n\n        def _browse(_e=None):\n            from tkinter import filedialog\n            picked = filedialog.askopenfilename(\n                parent=self, title="Pick your profile photo",\n                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.gif")])\n            if picked:\n                self._photo_entry.delete(0, "end")\n                self._photo_entry.insert(0, picked)\n                _preview()\n\n        self._avatar_canvas.bind("<Button-1>", _browse)\n        self._photo_entry.bind("<Return>", _preview)\n        panel_ui.style_button(\n            ctk.CTkButton(btns, text="Upload photo\\u2026", width=120,\n                          height=30, command=_browse), C, "primary"\n        ).pack(side="left")\n        btns.winfo_children()[-1].configure(text_color=on_accent)\n        panel_ui.style_button(\n            ctk.CTkButton(src, text="Preview", width=80, height=32,\n                          command=_preview), C, "ghost"\n        ).pack(side="left", padx=(6, 0))\n        panel_ui.style_button(\n            ctk.CTkButton(btns, text="✕", width=30, height=30,\n                          command=self._clear_photo), C, "danger"\n        ).pack(side="left", padx=(6, 0))\n        ctk.CTkLabel(btns, text="Remove photo", text_color=C["fg_faint"],\n                     font=("Segoe UI", 10)).pack(side="left", padx=(6, 0))\n\n        # ================= About you =================\n        box = _heading("About you")\n        g = _grid2(box)\n        _entry(g, 0, 0, "name", "Real name", "e.g. Ada Lovelace")\n        _entry(g, 0, 1, "dev_names", "Dev names / handles",\n               "comma-separated, e.g. alovelace, ada-dev")\n        bio_cell = ctk.CTkFrame(box, fg_color="transparent")\n        bio_cell.pack(fill="x", pady=(10, 0))\n        ctk.CTkLabel(bio_cell, text="Bio", text_color=C["fg_dim"],\n                     anchor="w", font=("Segoe UI", 10, "bold")).pack(\n            fill="x")\n        ctk.CTkLabel(bio_cell, text="Describe yourself \\u2014 this text is "\n                     "what Friday reads to know you.",\n                     text_color=C["fg_faint"], anchor="w",\n                     font=("Segoe UI", 10)).pack(fill="x")\n        self._bio_text = ctk.CTkTextbox(\n            bio_cell, height=110, fg_color=C["input"], text_color=C["fg"],\n            border_width=1, border_color=C["border"], corner_radius=8,\n            font=("Segoe UI", 11), wrap="word")\n        self._bio_text.pack(fill="x", pady=(4, 0))\n        if profile.get("bio"):\n            self._bio_text.insert("1.0", profile["bio"])\n        g2 = _grid2(box)\n        _entry(g2, 0, 0, "interests", "Interests",\n               "e.g. AI agents, music production, fintech")\n        _entry(g2, 0, 1, "goals", "Goals",\n               "e.g. ship a SaaS, pass finals, learn Rust")\n\n        # ================= Links =================\n        box = _heading("Links")\n        g = _grid2(box)\n        self._link_vars = {}\n        for i, (key, label, hint) in enumerate((\n                ("website", "Website", "https://yoursite.com"),\n                ("portfolio", "Portfolio", "https://"),\n                ("github", "GitHub", "https://github.com/username"),\n                ("twitter", "Twitter / X", "https://x.com/username"),\n                ("linkedin", "LinkedIn", "https://linkedin.com/in/username"),\n                ("telegram", "Telegram", "https://t.me/username"))):\n            ent = _field(g, i // 2, i % 2, label, hint)\n            value = (profile.get("links") or {}).get(key, "")\n            if value:\n                ent.insert(0, value)\n            self._link_vars[key] = ent\n\n        # ================= Emails =================\n        box = _heading("Emails")\n        _note(box, "Mailboxes Friday can work for you (read / send / "\n                   "sign-ups). Add as many as you have \\u2014 when a task "\n                   "needs mail and you\'ve added 2 or more, Friday asks "\n                   "which one to use. Passwords are encrypted, masked, "\n                   "and never reach any prompt. Setup only for now: no "\n                   "mail action happens yet.")\n        self._emails_host = ctk.CTkFrame(box, fg_color="transparent",\n                                         height=1)\n        self._emails_host.pack(fill="x", pady=(2, 0))\n        self._email_rows = []\n        for stored in (profile.get("emails") or []):\n            self._email_row(stored=stored)\n        add_e = ctk.CTkFrame(box, fg_color="transparent")\n        add_e.pack(fill="x", pady=(8, 0))\n        self._new_addr = _style_entry(ctk.CTkEntry(\n            add_e, height=34, placeholder_text="email address"))\n        self._new_addr.pack(side="left", fill="x", expand=True)\n        self._new_epass = _style_entry(ctk.CTkEntry(\n            add_e, height=34, placeholder_text="password", show="•"))\n        self._new_epass.pack(side="left", fill="x", expand=True,\n                             padx=(6, 0))\n        panel_ui.style_button(ctk.CTkButton(\n            add_e, text="+ Add email", width=100, height=34,\n            command=lambda: self._email_row(stored=None)), C\n        ).pack(side="left", padx=(8, 0))\n\n        # ================= Connect apps =================\n        box = _heading("Connect apps")\n        _note(box, "Apps you use (comma-separated) \\u2014 context so "\n                   "Friday knows your toolbox.")\n        self._apps_entry = _style_entry(ctk.CTkEntry(\n            box, height=34,\n            placeholder_text="e.g. VS Code, GitHub, Figma, Slack"))\n        self._apps_entry.pack(fill="x")\n        if profile.get("connected_apps"):\n            self._apps_entry.insert(0, ", ".join(profile["connected_apps"]))\n\n        # ================= GitHub authors =================\n        box = _heading("GitHub authors")\n        _note(box, "Commit identities for YOUR GitHub accounts (username "\n                   "+ email). A clone carrying one of these usernames "\n                   "commits as that author automatically -- no asking; "\n                   "other repos let you pick. The third field is that "\n                   "account\'s own ACCESS TOKEN for GitHub actions (masked, "\n                   "never prompted). Friday uses only the selected account\'s "\n                   "token from this section for commits/pushes/PRs; Web "\n                   "passwords is not a fallback for those actions.")\n        self._gh_host = ctk.CTkFrame(box, fg_color="transparent",\n                                     height=1)\n        self._gh_host.pack(fill="x", pady=(2, 0))\n        self._gh_rows = []\n        for stored in (profile.get("github_authors") or []):\n            self._gh_row(stored=stored)\n        add_g = ctk.CTkFrame(box, fg_color="transparent")\n        add_g.pack(fill="x", pady=(8, 0))\n        self._new_gh_user = _style_entry(ctk.CTkEntry(\n            add_g, height=34, placeholder_text="github username"))\n        self._new_gh_user.pack(side="left", fill="x", expand=True)\n        self._new_gh_mail = _style_entry(ctk.CTkEntry(\n            add_g, height=34, placeholder_text="author email"))\n        self._new_gh_mail.pack(side="left", fill="x", expand=True,\n                               padx=(6, 0))\n        self._new_gh_tok = _style_entry(ctk.CTkEntry(\n            add_g, height=34, placeholder_text="token (optional)", show="•"))\n        self._new_gh_tok.pack(side="left", fill="x", expand=True,\n                              padx=(6, 0))\n        self._token_eye(add_g, self._new_gh_tok).pack(side="left", padx=(4, 0))\n        panel_ui.style_button(ctk.CTkButton(\n            add_g, text="+ Add author", width=100, height=34,\n            command=lambda: self._gh_row(stored=None)), C\n        ).pack(side="left", padx=(8, 0))\n\n        # ================= Web passwords =================\n        box = _heading("Web passwords")\n        _note(box, "Stored encrypted inside Friday\'s local store and shown "\n                   "only as masks \\u2014 Friday never reads them into "\n                   "prompts or speaks them.")\n        self._creds_host = ctk.CTkFrame(box, fg_color="transparent",\n                                        height=1)\n        self._creds_host.pack(fill="x", pady=(2, 0))\n        self._cred_rows = []\n        for row in profile.get("credentials", []):\n            self._cred_row(stored=row)\n        add = ctk.CTkFrame(box, fg_color="transparent")\n        add.pack(fill="x", pady=(8, 0))\n        self._new_site = _style_entry(\n            ctk.CTkEntry(add, height=34, placeholder_text="site url"))\n        self._new_site.pack(side="left", fill="x", expand=True)\n        self._new_mail = _style_entry(\n            ctk.CTkEntry(add, height=34, placeholder_text="email (optional)"))\n        self._new_mail.pack(side="left", fill="x", expand=True,\n                            padx=(6, 0))\n        self._new_user = _style_entry(\n            ctk.CTkEntry(add, height=34, placeholder_text="username"))\n        self._new_user.pack(side="left", fill="x", expand=True,\n                            padx=(6, 0))\n        self._new_pass = _style_entry(\n            ctk.CTkEntry(add, height=34, placeholder_text="password",\n                         show="•"))\n        self._new_pass.pack(side="left", fill="x", expand=True,\n                            padx=(6, 0))\n        panel_ui.style_button(\n            ctk.CTkButton(add, text="Add", width=70, height=34,\n                          command=lambda: self._cred_row(stored=None)),\n            C, "ghost").pack(side="left", padx=(6, 0))\n\n        # breathing room above the pinned Save bar\n        ctk.CTkFrame(body, fg_color="transparent", height=16).pack()\n\n        # live header: name / handles / completeness follow the typing\n        for ent in list(self._profile_vars.values()) + \\\n                list(self._link_vars.values()) + [self._photo_entry]:\n            ent.bind("<KeyRelease>", _refresh_header, add="+")\n        self._bio_text.bind("<KeyRelease>", _refresh_header, add="+")\n        img0 = user_profile.avatar_photoimage(profile, size=photo_px,\n                                              master=self)\n        if img0 is not None:\n            self._photo_img = img0\n        _draw_avatar()\n        _refresh_header()\n        return page\n\n    def _clear_photo(self):\n        """The X next to the photo: drop the path AND the picture, back to\n        the initials placeholder."""\n        self._photo_entry.delete(0, "end")\n        self._photo_img = None\n        refresh = getattr(self, "_refresh_profile_header", None)\n        if refresh is not None:\n            try:\n                refresh()\n            except tk.TclError:\n                pass\n'
NEW_TEST = '"""Settings -> Profile layout: Profile is the first tab and the page the\npanel opens on; the photo header sits at the very top of the page; the\nSave bar is pinned outside the scroll area; photo preview/clear and the\nlive header work; saving still round-trips every field.\n\nRuns a REAL SettingsPanel against a temp memory folder (needs a display).\n"""\nimport os\nimport sys\nimport tempfile\nimport unittest\n\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nsys.path.insert(0, ROOT)\n\nimport memory  # noqa: E402\nimport user_profile  # noqa: E402\n\n\ndef _png(path, color=(200, 80, 40)):\n    from PIL import Image\n    Image.new("RGB", (64, 64), color).save(path)\n    return path\n\n\nclass TestProfileFirstLayout(unittest.TestCase):\n    @classmethod\n    def setUpClass(cls):\n        import tkinter as tk\n        try:\n            cls.root = tk.Tk()\n            cls.root.withdraw()\n        except tk.TclError:\n            raise unittest.SkipTest("no display")\n\n    @classmethod\n    def tearDownClass(cls):\n        cls.root.destroy()\n\n    def setUp(self):\n        self.tmp = tempfile.mkdtemp(prefix="profile_layout_")\n        self.prev = memory._memory_dir\n        memory.set_memory_dir(self.tmp)\n        self.photo = _png(os.path.join(self.tmp, "me.png"))\n        user_profile.save({**user_profile.default_profile(),\n                           "name": "Ada Lovelace", "dev_names": "ada, alove",\n                           "bio": "Analyst.", "photo": self.photo,\n                           "links": {**user_profile.default_profile()["links"],\n                                     "github": "https://github.com/ada"}})\n        self.panels = []\n\n    def tearDown(self):\n        for p in self.panels:\n            try:\n                p.destroy()\n            except Exception:  # noqa: BLE001\n                pass\n        memory.set_memory_dir(self.prev)\n\n    def _panel(self, **kw):\n        import settings_panel\n        p = settings_panel.SettingsPanel(\n            self.root, on_minimize=lambda: None, on_close=lambda: None,\n            corner_index=0, initial_content=None, bounds=None, **kw)\n        self.panels.append(p)\n        for _ in range(20):\n            self.root.update()\n        return p\n\n    def _press_return(self, entry):\n        # key events go to the focused widget, so focus the field first\n        entry._entry.focus_force()\n        self.root.update()\n        entry._entry.event_generate("<Return>")\n\n    def test_profile_is_first_tab_and_default_page(self):\n        p = self._panel()\n        self.assertEqual(list(p._page_keys), ["Profile", "Keys & Connect"])\n        self.assertEqual(p._page, "profile")\n        self.assertEqual(p._seg.get(), "Profile")\n        self.assertTrue(p._profile_host.winfo_ismapped())\n        self.assertFalse(p._keys_host.winfo_ismapped())\n        # Check all belongs to the keys page only\n        self.assertFalse(p._check_all_btn.winfo_ismapped())\n\n    def test_keys_page_still_reachable(self):\n        p = self._panel(initial_page="keys")\n        self.assertEqual(p._page, "keys")\n        self.assertTrue(p._check_all_btn.winfo_ismapped())\n        p.show_page("profile")\n        self.root.update()\n        self.assertTrue(p._profile_host.winfo_ismapped())\n        self.assertIn("profile", p._subheader_lbl.cget("text").lower())\n        p.show_page("keys")\n        self.assertIn("Keys are masked", p._subheader_lbl.cget("text"))\n\n    def test_photo_header_is_at_the_top(self):\n        p = self._panel()\n        av_y = p._avatar_canvas.winfo_rooty()\n        # above every field on the page\n        for ent in list(p._profile_vars.values()) + list(p._link_vars.values()):\n            self.assertLess(av_y, ent.winfo_rooty())\n        self.assertIsNotNone(p._photo_img, "saved photo should load")\n        self.assertEqual(p._hdr_name.cget("text"), "Ada Lovelace")\n        self.assertIn("@ada", p._hdr_handles.cget("text"))\n        self.assertIn("@alove", p._hdr_handles.cget("text"))\n\n    def test_save_bar_is_pinned_outside_the_scroll_area(self):\n        p = self._panel()\n        status = p._profile_status\n        self.assertTrue(status.winfo_ismapped())\n        # not inside the scrolling canvas\n        w = status\n        while w is not None and w is not p._profile_canvas:\n            w = w.master\n        self.assertIsNone(w)\n\n    def test_live_header_and_photo_clear_and_preview(self):\n        p = self._panel()\n        name = p._profile_vars["name"]\n        name.delete(0, "end")\n        name.insert(0, "Grace Hopper")\n        p._refresh_profile_header()\n        self.assertEqual(p._hdr_name.cget("text"), "Grace Hopper")\n        p._clear_photo()\n        self.assertIsNone(p._photo_img)\n        self.assertEqual(p._photo_entry.get(), "")\n        texts = [p._avatar_canvas.itemcget(i, "text")\n                 for i in p._avatar_canvas.find_all()\n                 if p._avatar_canvas.type(i) == "text"]\n        self.assertIn("GH", texts)      # initials placeholder\n        p._photo_entry.insert(0, self.photo)\n        self._press_return(p._photo_entry)\n        self.root.update()\n        self.assertIsNotNone(p._photo_img)\n        p._photo_entry.delete(0, "end")\n        p._photo_entry.insert(0, os.path.join(self.tmp, "nope.png"))\n        self._press_return(p._photo_entry)\n        self.root.update()\n        self.assertIn("Couldn\'t load", p._profile_status.cget("text"))\n\n    def test_save_round_trip_unchanged(self):\n        p = self._panel()\n        p._profile_vars["goals"].insert(0, "ship it")\n        p._link_vars["website"].insert(0, "https://ada.dev")\n        p._apps_entry.insert(0, "VS Code, Figma")\n        p._save_profile()\n        got = user_profile.load()\n        self.assertEqual(got["name"], "Ada Lovelace")\n        self.assertEqual(got["bio"], "Analyst.")\n        self.assertEqual(got["goals"], "ship it")\n        self.assertEqual(got["photo"], self.photo)\n        self.assertEqual(got["links"]["website"], "https://ada.dev")\n        self.assertEqual(got["links"]["github"], "https://github.com/ada")\n        self.assertEqual(got["connected_apps"], ["VS Code", "Figma"])\n        self.assertIn("Saved", p._profile_status.cget("text"))\n\n    def test_empty_lists_take_no_space_and_grow_with_rows(self):\n        p = self._panel()\n        self.assertLess(p._emails_host.winfo_height(), 20)\n        p._new_addr.insert(0, "ada@example.com")\n        p._new_epass.insert(0, "pw")\n        p._email_row(stored=None)\n        for _ in range(10):\n            self.root.update()\n        self.assertGreater(p._emails_host.winfo_height(), 15)\n        self.assertEqual(len(p._email_rows), 1)\n\n    def test_completeness_meter(self):\n        p = self._panel()\n        # name, handles, bio, photo, a link = 5 of 7\n        self.assertIn("71%", p._hdr_meter_lbl.cget("text"))\n        p._profile_vars["interests"].insert(0, "math")\n        p._profile_vars["goals"].insert(0, "engines")\n        p._refresh_profile_header()\n        self.assertEqual(p._hdr_meter_lbl.cget("text"), "Profile complete")\n\n\nif __name__ == "__main__":\n    unittest.main(verbosity=2)\n'
TEST_EDITS = [('dev_tests/test_touchpad_scroll.py', 'panel = gui(lambda: mgr._panels["settings"]["frame"])\ncanvas = gui(lambda: panel._canvas)', 'panel = gui(lambda: mgr._panels["settings"]["frame"])\ngui(lambda: panel.show_page("keys"))   # Settings opens on Profile now\ntime.sleep(0.5)\ncanvas = gui(lambda: panel._canvas)'), ('dev_tests/test_user_profile.py', 'self.assertIn(\'self._page_keys = {"Keys & Connect": "keys", \'\n                      \'"Profile": "profile"}\', self.src)', 'self.assertIn(\'self._page_keys = {"Profile": "profile", \'\n                      \'"Keys & Connect": "keys"}\', self.src)'), ('dev_tests/test_user_profile.py', 'corner_index=0, initial_content=None, bounds=None)\n            for _ in range(30):', 'corner_index=0, initial_content=None, bounds=None,\n                initial_page="keys")\n            for _ in range(30):')]
REPLACED = ("show_page", "_build_profile_page", "_clear_photo")
notes, written = [], []


def path(rel):
    return os.path.join(HERE, *rel.split("/"))


def read(rel):
    with open(path(rel), encoding="utf-8", newline="") as fh:
        return fh.read()


def write(rel, text, dry):
    if dry:
        return
    p = path(rel)
    if os.path.exists(p) and not os.path.exists(p + BAK):
        shutil.copy2(p, p + BAK)
    elif not os.path.exists(p):
        open(p + BAK + "-new", "w").close()    # marker: file was created
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    written.append(rel)


def newline_of(text):
    return "\r\n" if "\r\n" in text else "\n"


def names_used(fn_src):
    """self._attrs assigned, _heading("...") titles, profile keys read."""
    tree = ast.parse(fn_src)
    attrs, heads, keys = set(), set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    attrs.add(t.attr)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "_heading" and n.args \
                and isinstance(n.args[0], ast.Constant):
            heads.add(str(n.args[0].value))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and isinstance(n.func.value, ast.Name) \
                and n.func.value.id == "profile" and n.args \
                and isinstance(n.args[0], ast.Constant):
            keys.add(str(n.args[0].value))
        if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                and n.value.id == "profile" and isinstance(n.slice, ast.Constant):
            keys.add(str(n.slice.value))
    # every identifier-like string (field keys such as "pronouns" passed to
    # _entry(), link keys in the Links tuple, ...)
    words = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
             and isinstance(n.value, str)
             and re.fullmatch(r"[a-z][a-z0-9_]*", n.value)}
    return attrs, heads, keys, words


def fix_panel(dry):
    rel = "settings_panel.py"
    raw = read(rel)
    nl = newline_of(raw)
    text = raw.replace("\r\n", "\n")
    if MARK in text:
        notes.append(f"{rel}: already restyled -- skipped")
        return
    tree = ast.parse(text)
    cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                isinstance(b, ast.FunctionDef) and b.name == "_build_profile_page"
                for b in node.body):
            cls = node
            break
    if cls is None:
        raise SystemExit(f"{rel}: can't find the class with _build_profile_page() "
                         "-- send me this file.")
    methods = {b.name: b for b in cls.body if isinstance(b, ast.FunctionDef)}
    for need in ("show_page", "_build_profile_page", "__init__"):
        if need not in methods:
            raise SystemExit(f"{rel}: can't find {need}() -- send me this file.")
    lines = text.splitlines(keepends=True)

    def span(fn):
        start = (fn.decorator_list[0].lineno if fn.decorator_list else fn.lineno) - 1
        return start, fn.end_lineno

    # --- safety: never drop something the user's Profile page has ---
    s, e = span(methods["_build_profile_page"])
    theirs = names_used(textwrap.dedent("".join(lines[s:e])))
    new_tree = ast.parse("class _X:\n" + NEW_METHODS)
    new_fn = next(b for b in new_tree.body[0].body
                  if isinstance(b, ast.FunctionDef) and b.name == "_build_profile_page")
    mine = names_used(ast.unparse(new_fn))
    tolerated = {"_avatar_holder"}           # old label, replaced by the canvas
    missing = [("field", a) for a in sorted(theirs[0] - mine[0] - tolerated)]
    # "Who you are" became "About you"; "Photo" became the header card
    missing += [("section", h) for h in sorted(theirs[1] - mine[1]
                                               - {"Who you are", "Photo"})]
    missing += [("profile key", k) for k in sorted(theirs[2] - mine[2])]
    # "caret"/"border_hi" were colours of the old plain-Tk bio box
    missing += [("key/name", w) for w in sorted(theirs[3] - mine[3]
                                                - {"caret", "border_hi"}
                                                - theirs[2])]
    if missing:
        raise SystemExit(
            f"{rel}: your Profile page has things this redesign doesn't include:\n  "
            + "\n  ".join(f"{k}: {v}" for k, v in missing)
            + "\nNothing was changed. Send me settings_panel.py and I'll fold them in.")
    if "_avatar_holder" in "".join(
            l for i, l in enumerate(lines)
            if not any(span(methods[m])[0] <= i < span(methods[m])[1]
                       for m in REPLACED if m in methods)):
        raise SystemExit(f"{rel}: _avatar_holder is used outside the profile page "
                         "-- send me this file.")

    # --- edits, applied bottom-up so earlier line numbers stay valid ---
    indent = re.match(r"\s*", lines[methods["show_page"].lineno - 1]).group(0)
    block = "".join((indent + l[4:]) if l.startswith("    ") else l
                    for l in NEW_METHODS.splitlines(keepends=True))
    blocks = re.split(r"\n(?=" + re.escape(indent) + r"def )", block)
    new_by_name = {}
    for b in blocks:
        m = re.match(r"\s*def (\w+)\(", b)
        new_by_name[m.group(1)] = b.rstrip("\n") + "\n"
    edits = []    # (start, end, replacement)
    edits.append((*span(methods["show_page"]),
                  new_by_name["show_page"] + "\n" + new_by_name["_retitle_subheader"]))
    edits.append((*span(methods["_build_profile_page"]),
                  new_by_name["_build_profile_page"]))
    if "_clear_photo" in methods:
        edits.append((*span(methods["_clear_photo"]), new_by_name["_clear_photo"]))
    else:
        s2, e2 = span(methods["_build_profile_page"])
        edits.append((e2, e2, "\n" + new_by_name["_clear_photo"]))
    init = methods["__init__"]
    done_keys = done_start = False
    for n in ast.walk(init):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Attribute) and t.attr == "_page_keys"
                for t in n.targets):
            pad = re.match(r"\s*", lines[n.lineno - 1]).group(0)
            edits.append((n.lineno - 1, n.end_lineno,
                          pad + 'self._page_keys = {"Profile": "profile", '
                          '"Keys & Connect": "keys"}\n'))
            done_keys = True
        if isinstance(n, ast.If) and "initial_page" in ast.unparse(n.test) \
                and "show_page" in ast.unparse(n):
            pad = re.match(r"\s*", lines[n.lineno - 1]).group(0)
            edits.append((n.lineno - 1, n.end_lineno,
                          f'{pad}# Profile is the first page Settings opens on\n'
                          f'{pad}if initial_page == "keys":\n'
                          f'{pad}    self.show_page("keys")\n'
                          f'{pad}else:\n'
                          f'{pad}    self.show_page("profile")\n'))
            done_start = True
    if not (done_keys and done_start):
        raise SystemExit(f"{rel}: can't find the page switcher setup in __init__ "
                         "-- send me this file.")
    for s, e, rep in sorted(edits, key=lambda t: t[0], reverse=True):
        lines[s:e] = [rep]
    out = "".join(lines)
    ast.parse(out)
    notes.append(f"{rel}: switcher is now [Profile | Keys & Connect], opens on "
                 "Profile; Profile page redesigned (photo header on top, cards, "
                 "pinned Save)")
    write(rel, out.replace("\n", nl), dry)


def fix_tests(dry):
    for rel, old, new in TEST_EDITS:
        if not os.path.exists(path(rel)):
            continue
        raw = read(rel)
        nl = newline_of(raw)
        t = raw.replace("\r\n", "\n")
        if new in t:
            continue
        if old not in t:
            notes.append(f"{rel}: an old test line wasn't found (fine if you edited it)")
            continue
        t = t.replace(old, new, 1)
        notes.append(f"{rel}: updated one test to the new page order")
        write(rel, t.replace("\n", nl), dry)
    rel = "dev_tests/test_profile_page_layout.py"
    if os.path.isdir(path("dev_tests")) and not os.path.exists(path(rel)):
        notes.append(f"{rel}: added (checks the new layout on a real panel)")
        write(rel, NEW_TEST, dry)


def undo():
    n = 0
    for root, _dirs, files in os.walk(HERE):
        for f in files:
            p = os.path.join(root, f)
            if f.endswith(BAK):
                shutil.move(p, p[:-len(BAK)])
                n += 1
            elif f.endswith(BAK + "-new"):
                target = p[:-len(BAK + "-new")]
                if os.path.exists(target):
                    os.remove(target)
                os.remove(p)
                n += 1
    print(f"Restored {n} file(s)." if n else "No backups found -- nothing to undo.")


def main():
    if "--undo" in sys.argv:
        undo()
        return
    dry = "--dry-run" in sys.argv
    if not os.path.exists(path("settings_panel.py")):
        raise SystemExit("Run this from the floating-agent-python folder "
                         "(the one with settings_panel.py).")
    try:
        fix_panel(dry)
        fix_tests(dry)
        if not dry:
            for rel in written:
                py_compile.compile(path(rel), doraise=True)
    except BaseException as exc:
        for rel in written:
            p = path(rel)
            if os.path.exists(p + BAK):
                shutil.move(p + BAK, p)
            elif os.path.exists(p + BAK + "-new"):
                os.remove(p)
                os.remove(p + BAK + "-new")
        if written:
            print("Nothing was changed (all files restored).")
        if isinstance(exc, SystemExit):
            raise
        raise SystemExit(f"Error: {exc!r} -- nothing was changed.")
    print("DRY RUN -- would do:" if dry else "Done:")
    for n in notes:
        print("  - " + n)
    if not dry and written:
        print("\nBackups: *" + BAK + "   (undo with: python apply_profile_restyle.py --undo)")
        print("Verify:   python dev_tests/test_profile_page_layout.py")


if __name__ == "__main__":
    main()
