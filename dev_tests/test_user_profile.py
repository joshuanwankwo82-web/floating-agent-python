"""F33 battery: user_profile store + Profile-in-Settings wiring.

Runtime parts prove the encrypted store round-trips, never leaks
plaintext secrets to disk, normalizes junk, keeps credentials OUT of
any prompt text, masks at fixed length, and decodes a real PNG avatar
(plus degrades to None on garbage). Static pins lock the cross-file
seams: Settings submenu/page flip, "open my profile" redirect,
system-prompt injection, and the chat header presence badge.
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import user_profile  # noqa: E402
import memory  # noqa: E402


def _src(name):
    with open(os.path.join(ROOT, name)) as f:
        return f.read()


SAMPLE = {
    "name": "Chidi Okafor",
    "dev_names": "chidi-dev, okaf",
    "bio": "Full-stack dev in Nsukka; Python and FastAPI.",
    "photo": "",
    "links": {"website": "https://chidi.dev", "github": "https://gh/ch",
              "twitter": "", "linkedin": "", "telegram": "",
              "portfolio": ""},
    "connected_apps": ["github", "gmail"],
    "credentials": [{"site": "github.com", "username": "chidi",
                     "password": "s3cr3t-TOP-SECRET"}],
}


class TestStore(unittest.TestCase):
    """Encrypted-at-rest behavior, pinned against a temp memory folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="up_mem_")
        self.patch = mock.patch.object(memory, "_ensure_folder",
                                       side_effect=lambda name: os.path.join(
                                           self.tmp, name))
        self.patch.start()
        os.makedirs(os.path.join(self.tmp, "settings"), exist_ok=True)

    def tearDown(self):
        self.patch.stop()

    def test_round_trip(self):
        user_profile.save(SAMPLE)
        back = user_profile.load()
        self.assertEqual(back["name"], "Chidi Okafor")
        self.assertEqual(back["credentials"][0]["password"],
                         "s3cr3t-TOP-SECRET")
        self.assertEqual(back["links"]["github"], "https://gh/ch")

    def test_file_is_sibling_of_settings_and_encrypted(self):
        user_profile.save(SAMPLE)
        path = os.path.join(self.tmp, "settings",
                            "user_profile.json.enc")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            blob = f.read()
        # Fernet payloads are base64-ish; plaintext must NOT appear.
        for secret in (b"Chidi Okafor", b"s3cr3t-TOP-SECRET",
                       b"github.com", b"chidi"):
            self.assertNotIn(secret, blob)

    def test_email_round_trip_encrypted(self):
        with_email = dict(SAMPLE, emails=[
            {"address": "chidi@x.ng", "password": "app-pass-SECRET"},
            {"address": "chidi2@y.io", "password": "second"}])
        user_profile.save(with_email)
        back = user_profile.load()
        self.assertEqual(back["emails"][0]["password"],
                         "app-pass-SECRET")
        self.assertEqual(len(back["emails"]), 2)
        path = os.path.join(self.tmp, "settings", "user_profile.json.enc")
        with open(path, "rb") as f:
            blob = f.read()
        self.assertNotIn(b"app-pass-SECRET", blob)
        self.assertNotIn(b"chidi@x.ng", blob)

    def test_credential_round_trip_keeps_email_field(self):
        creds = dict(SAMPLE, credentials=[{
            "site": "https://shop.ng", "email": "chidi@x.ng",
            "username": "chidi", "password": "pw-SECRET"}])
        user_profile.save(creds)
        back = user_profile.load()
        self.assertEqual(back["credentials"][0]["email"], "chidi@x.ng")

    def test_github_authors_round_trip(self):
        prof = dict(SAMPLE)
        prof["github_authors"] = [{"username": "ada",
                                   "email": "ada@x.io",
                                   "token": "ghp_ADA"}]
        user_profile.save(prof)
        back = user_profile.load()
        self.assertEqual(back["github_authors"],
                         [{"username": "ada", "email": "ada@x.io",
                           "token": "ghp_ADA"}])

    def test_load_missing_file_returns_defaults(self):
        prof = user_profile.load()
        self.assertEqual(prof, user_profile.default_profile())

    def test_corrupt_file_returns_defaults(self):
        path = os.path.join(self.tmp, "settings",
                            "user_profile.json.enc")
        with open(path, "wb") as f:
            f.write(b"not fernet at all")
        self.assertEqual(user_profile.load(),
                         user_profile.default_profile())


class TestNormalize(unittest.TestCase):
    def test_github_authors_shape(self):
        # default empty; junk coerced; username is required, email and
        # token optional-but-cleaned; order preserved; F47: token rides
        # the row and NEVER survives with pasted whitespace
        self.assertEqual(user_profile.default_profile()
                         ["github_authors"], [])
        out = user_profile._normalize({"github_authors": [
            {"username": "ada", "email": "ada@x.io"},
            {"username": "  ", "email": "dropped@x.io"},
            42,
            {"username": "chidi-dev", "email": "",
             "token": "  ghp_CHIDI\n"}]})
        self.assertEqual(out["github_authors"], [
            {"username": "ada", "email": "ada@x.io", "token": ""},
            {"username": "chidi-dev", "email": "", "token": "ghp_CHIDI"}])

    def test_author_tokens_never_join_the_prompt(self):
        prof = dict(SAMPLE)
        prof["github_authors"] = [{"username": "ada", "email": "a@x.io",
                                   "token": "ghp_NEVERLEAK"}]
        text = user_profile.prompt_summary(prof)
        self.assertIn("ada", text)
        self.assertNotIn("ghp_NEVERLEAK", text)
        self.assertNotIn("token", text.lower())

    def test_authors_join_the_prompt_as_identities(self):
        prof = dict(SAMPLE)
        prof["github_authors"] = [{"username": "ada", "email": "a@x.io"},
                                  {"username": "chidi-dev",
                                   "email": "c@y.io"}]
        text = user_profile.prompt_summary(prof)
        self.assertIn("ada", text)
        self.assertIn("chidi-dev", text)
        # nothing secret: the identities are public by definition
        self.assertNotIn("password", text.lower())

    def test_email_shape(self):
        prof = user_profile.default_profile()
        self.assertEqual(prof["emails"], [])
        self.assertNotIn("email", prof)
        # canonical list coerces junk
        out = user_profile._normalize(
            {"emails": [{"address": "a@b.c", "password": 7}, "bad"]})
        self.assertEqual(out["emails"],
                         [{"address": "a@b.c", "password": "7"}])
        # legacy single-dict migrates; provider is DROPPED (F40)
        out = user_profile._normalize(
            {"email": {"address": "old@x.io", "provider": "gmail",
                       "app_password": "app-pass"}})
        self.assertEqual(out["emails"], [{"address": "old@x.io",
                                          "password": "app-pass"}])
        junk = user_profile._normalize({"emails": "junk"})
        self.assertEqual(junk["emails"], [])

    def test_junk_coerced(self):
        data = {"name": 42, "bio": None, "links": "nope",
                "connected_apps": ["ok", 5, "  "],
                "credentials": ["bad", {"site": "s", "username": 1,
                                        "password": True},
                                {"username": "orphan"}]}  # no site: drop
        out = user_profile._normalize(data)
        self.assertEqual(out["name"], "42")
        self.assertEqual(out["bio"], "None")
        self.assertEqual(out["links"]["website"], "")
        self.assertEqual(out["connected_apps"], ["ok"])
        self.assertEqual(out["credentials"],
                         [{"site": "s", "email": "", "username": "1",
                           "password": "True"}])

    def test_unknown_keys_dropped(self):
        out = user_profile._normalize({**SAMPLE, "evil": "x",
                                       "links": {"website": "w",
                                                 "hack": "y"}})
        self.assertNotIn("evil", out)
        self.assertNotIn("hack", out["links"])


class TestPromptSafety(unittest.TestCase):
    def test_prompt_never_contains_secrets(self):
        text = user_profile.prompt_summary(SAMPLE)
        self.assertIn("Chidi Okafor", text)
        self.assertIn("chidi-dev", text)
        self.assertIn("https://gh/ch", text)
        for secret in ("s3cr3t-TOP-SECRET", "github.com",
                       "username", "password", "credential"):
            self.assertNotIn(secret, text)

    def test_prompt_keeps_email_safe(self):
        prof = dict(SAMPLE, emails=[{"address": "chidi@x.ng",
                                    "password": "PW-NEVER-SHOWN"}])
        text = user_profile.prompt_summary(prof)
        self.assertIn("chidi@x.ng", text)
        self.assertNotIn("PW-NEVER-SHOWN", text)
        self.assertNotIn("password", text.lower())
        # multiple mailboxes: pluralized prompt line names them all
        prof2 = dict(SAMPLE, emails=[{"address": "a@x.io",
                                      "password": "s1"},
                                     {"address": "b@y.io",
                                      "password": "s2"}])
        text2 = user_profile.prompt_summary(prof2)
        self.assertIn("a@x.io", text2)
        self.assertIn("b@y.io", text2)
        self.assertIn("emails:", text2)
        # email-only profile still counts as a real profile
        only = user_profile.prompt_summary(
            {"name": "", "dev_names": "", "bio": "", "links": {},
             "credentials": [], "connected_apps": [], "interests": "",
             "goals": "",
             "emails": [{"address": "me@x.io", "password": "s"}]})
        self.assertIn("me@x.io", only)
        # legacy callers (single-dict shape) migrate on the fly
        legacy = user_profile.prompt_summary(
            {"email": {"address": "old@z.io", "app_password": "s3"}})
        self.assertIn("old@z.io", legacy)
        self.assertNotIn("s3", legacy)

    def test_interests_and_goals_style_the_prompt(self):
        prof = dict(SAMPLE, interests="AI agents, fintech",
                    goals="ship a SaaS this year")
        text = user_profile.prompt_summary(prof)
        self.assertIn("AI agents, fintech", text)
        self.assertIn("ship a SaaS this year", text)
        # junk normalizes, empties keep the profile silent
        self.assertEqual(
            user_profile._normalize({"interests": 3})["interests"], "3")
        self.assertEqual(user_profile.default_profile()["goals"], "")

    def test_empty_profile_is_silence(self):
        self.assertEqual(user_profile.prompt_summary(
            user_profile.default_profile()), "")

    def test_max_chars_budget(self):
        big = dict(SAMPLE, bio="x" * 5000)
        self.assertLessEqual(len(user_profile.prompt_summary(big)), 600)

    def test_mask_fixed_length(self):
        m = user_profile.mask("anything")
        self.assertEqual(m, "\u2022" * 10)
        self.assertEqual(user_profile.mask(""), m)
        self.assertEqual(user_profile.mask("x" * 200), m)

    def test_display_name(self):
        self.assertEqual(user_profile.display_name(SAMPLE),
                         "Chidi Okafor")
        self.assertEqual(user_profile.display_name(
            user_profile.default_profile()), "")


class TestAvatar(unittest.TestCase):
    def test_garbage_path_and_url_return_none(self):
        prof = dict(SAMPLE, photo="/definitely/not/here.png")
        self.assertIsNone(user_profile.avatar_photoimage(prof))
        prof = dict(SAMPLE, photo="http://127.0.0.1:1/x.png")
        self.assertIsNone(user_profile.avatar_photoimage(prof))
        prof = dict(SAMPLE, photo="")
        self.assertIsNone(user_profile.avatar_photoimage(prof))

    def test_real_png_decodes(self):
        try:
            import tkinter as tk
            from PIL import Image
        except Exception:
            self.skipTest("no tk/PIL in this env")
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display")
        try:
            png = os.path.join(tempfile.mkdtemp(prefix="up_png_"),
                               "me.png")
            Image.new("RGB", (40, 40), (200, 30, 30)).save(png)
            prof = dict(SAMPLE, photo=png)
            img = user_profile.avatar_photoimage(prof, size=26)
            self.assertIsNotNone(img)
            self.assertEqual(img.width(), 26)
            root.update_idletasks()
        finally:
            root.destroy()


class TestJumpButtonsRuntime(unittest.TestCase):
    def test_top_and_bottom_fly_on_the_real_panel(self):
        try:
            import tkinter as tk
        except Exception:
            self.skipTest("no tk")
        try:
            root = tk.Tk(); root.withdraw()
        except tk.TclError:
            self.skipTest("no display")
        tmp = tempfile.mkdtemp(prefix="up_jump_")
        prev_dir = memory._memory_dir
        memory.set_memory_dir(tmp)
        try:
            import settings_store
            import settings_panel
            data = {f: [] for f in settings_store.FACULTIES}
            for i in range(8):
                settings_store.add_model(data, "Thinking", f"m{i}",
                                         f"model-{i}:free", "K" * 40)
            settings_store.save(data)
            panel = settings_panel.SettingsPanel(
                root, on_minimize=lambda: None, on_close=lambda: None,
                corner_index=0, initial_content=None, bounds=None,
                initial_page="keys")
            for _ in range(30):
                root.update(); root.update_idletasks()
            btn_top, btn_bottom = panel._jump_btns["keys"]
            canvas = panel._canvas
            self.assertEqual(canvas.yview()[0], 0.0)
            # auto-visibility: at the top, only 'Bottom' is offered
            self.assertEqual(btn_bottom.winfo_manager(), "pack")
            self.assertEqual(btn_top.winfo_manager(), "")
            btn_bottom.invoke()
            end = time.time() + 1.2
            while time.time() < end:
                root.update(); root.update_idletasks()
                time.sleep(0.01)
            top_after = canvas.yview()[0]
            self.assertGreater(top_after, 0.5,
                               "Bottom pill must fly far down the page")
            # now at the bottom: 'Top' appears, 'Bottom' hides
            for _ in range(6):
                root.update(); time.sleep(0.4)
            self.assertEqual(btn_top.winfo_manager(), "pack")
            self.assertEqual(btn_bottom.winfo_manager(), "")
            btn_top.invoke()
            end = time.time() + 1.2
            while time.time() < end:
                root.update(); root.update_idletasks()
                time.sleep(0.01)
            self.assertAlmostEqual(canvas.yview()[0], 0.0, places=2,
                                   msg="Top pill must glide home")
            panel.destroy()
        finally:
            memory.set_memory_dir(prev_dir)
            root.destroy()


class TestSettingsWiringPins(unittest.TestCase):
    SRC = "settings_panel.py"

    def setUp(self):
        self.src = _src(self.SRC)

    def test_nav_and_pages(self):
        self.assertIn('"Keys & Connect"', self.src)
        self.assertIn('"Profile"', self.src)
        self.assertIn("def show_page(self, name):", self.src)
        self.assertIn("initial_page", self.src)
        self.assertIn("_build_profile_page", self.src)

    def test_f35_rows_never_clip_actions(self):
        # actions are packed RIGHT before the info side fills -- pack
        # feeds side-packed children first, so Check/Edit/Remove cannot
        # be clipped however narrow the centered column gets
        self.assertIn('actions.pack(side="right"', self.src)
        self.assertIn('info.pack(side="left", fill="x", expand=True',
                      self.src)
        self.assertIn('info.bind("<Configure>", _rewrap', self.src)
        self.assertIn("wraplength=max(120", self.src)

    def test_f39_vault_api_pins(self):
        up = _src("user_profile.py")
        self.assertIn(
            "def add_web_credential(site, username, password, email=", up)
        self.assertIn("def remove_web_credential(site):", up)
        self.assertIn("def suggest_password(length=16):", up)
        # upsert replaces by SITE regardless of case
        self.assertIn('.get("site", "").lower() != site.lower()', up)
        # generated passwords must cover all four char classes
        self.assertIn('string.digits, "!@#$%^&*()-_=+"', up)

    def test_f35_multi_select_removal(self):
        self.assertIn("self._selected_ids = set()", self.src)
        self.assertIn("ctk.CTkCheckBox(", self.src)
        self.assertIn("def _toggle_sel(self, faculty, entry, var):",
                      self.src)
        self.assertIn("def _set_all_selection(self, on):", self.src)
        self.assertIn("def _remove_selected(self):", self.src)
        self.assertIn('text="Remove selected (0)"', self.src)
        self.assertIn('Remove selected ({n})', self.src)
        self.assertIn('text="All", width=54', self.src)
        self.assertIn('text="None", width=54', self.src)
        # removal reuses the encrypted store's per-id remover per selection
        self.assertIn("settings_store.remove_model(self._data, faculty, entry_id)",
                      self.src)

    def test_f40_credential_format_and_confirms(self):
        # record format: site url, email, username, password
        s = self.src
        self.assertIn('"site": site,', s)
        self.assertIn('"email": self._new_mail.get(),', s)
        self.assertIn('row.get("email", "")', s)
        self.assertIn("self._new_mail", s)
        # removal asks first -- web passwords AND emails alike
        # (only substring pins here: the exact wording was line-wrapped
        # by the source's own formatter during F35's heredoc pipeline)
        i_del = s.index("def _del_cred(self, frame):")
        seg = s[i_del:i_del + 900]
        self.assertIn("messagebox.askyesno(", seg)
        self.assertIn('Are you sure you want to remove the web password',
                      seg)
        self.assertIn("def _del_email(self, frame):", s)
        seg2 = s[s.index("def _del_email(self, frame):"):]
        self.assertIn("messagebox.askyesno(", seg2[:900])
        # multi-email editor: rows + add inputs
        self.assertIn("def _email_row(self, stored):", s)
        self.assertIn("self._new_addr", s)
        self.assertIn("self._new_epass", s)
        self.assertIn('_heading("Emails")', s)
        self.assertIn('profile["emails"] = [dict(r["row"]) '
                      'for r in self._email_rows]', s)
        # the single-email fields are GONE
        self.assertNotIn("self._email_addr", s)
        self.assertNotIn("self._email_prov", s)

    def test_f35_photo_clear_x_and_email_section(self):
        self.assertIn("def _clear_photo(self):", self.src)
        self.assertIn("text=" + chr(34) + chr(0x2715) + chr(34) + ", width=30,", self.src)
        self.assertIn("_clear_photo", self.src)
        # F39: masked by DEFAULT, "Show" reveals locally to the user only
        self.assertIn('text="Show"', self.src)
        self.assertIn("def _toggle_reveal(btn=None):", self.src)
        self.assertIn("pass_lbl", self.src)

    def test_f34_top_segment_switcher_and_panel_fitting_content(self):
        # nav moved to a horizontal customtkinter-segmented top switcher
        self.assertIn("ctk.CTkSegmentedButton(", self.src)
        self.assertIn('self._page_keys = {"Profile": "profile", '
                      '"Keys & Connect": "keys"}', self.src)
        # page flips keep the switcher's active segment honest
        self.assertIn("self._seg.set(self._page_labels[name])", self.src)
        # both scroll pages follow the PANEL width (F36: the user saw
        # dead margins + clipped ids at the old fixed 660px column),
        # capped + centered only past a wide readability ceiling; the
        # constraint is the canvas WINDOW width (pack won't clamp
        # children), and centering never goes negative
        self.assertEqual(self.src.count("min(e.width, 660)"), 0)
        self.assertIn("_CONTENT_CAP = 1280", self.src)
        self.assertEqual(
            self.src.count("min(e.width - 2 * _CONTENT_PAD, "
                           "_CONTENT_CAP)"), 2)
        # (pin the width-arg form: bare "itemconfigure(" self-matches
        # the explanatory comment next to the call)
        self.assertEqual(self.src.count("itemconfigure("), 3)
        self.assertEqual(self.src.count(", width=w)"), 2)
        self.assertEqual(
            self.src.count("max(0, (e.width - w) // 2)"), 2)
        # every text field rides one dark input-well style; the bio
        # textbox wears the same border palette (never tk's white focus
        # ring default)
        self.assertIn("def _style_entry(entry):", self.src)
        self.assertGreaterEqual(self.src.count("_style_entry("), 8)
        self.assertIn("highlightthickness=1", self.src)

    def test_both_canvases_edge_scroll(self):
        self.assertEqual(
            self.src.count("touchpad_scroll.attach_edge_autoscroll("), 2)

    def test_f41_github_authors_ui(self):
        s = self.src
        self.assertIn('_heading("GitHub authors")', s)
        self.assertIn("def _gh_row(self, stored):", s)
        self.assertIn("def _del_gh(self, frame):", s)
        # removal asks first (same covenant as the vault rows)
        seg = s[s.index("def _del_gh(self, frame):"):][:900]
        self.assertIn("messagebox.askyesno(", seg)
        self.assertIn("self._new_gh_user", s)
        self.assertIn("self._new_gh_mail", s)
        self.assertIn("for r in self._gh_rows]", s)
        # F47: per-author token field, masked, saved encrypted at rest
        self.assertIn("self._new_gh_tok", s)
        self.assertIn('placeholder_text="token (optional)"', s)
        self.assertIn('show="•"', s)
        self.assertIn('"token": r["tok"].get().strip()', s)
        self.assertIn('"tok": tok', s)

    def test_f41_check_all_scoped_to_keys_page(self):
        s = self.src
        i = s.index('self._check_all_btn = panel_ui.style_button(')
        assert 'pack(' not in s[i:i+260].split(")")[0]
        i_sp = s.index("def show_page(self, name):", i)
        seg = s[i_sp:i_sp + 1200]
        self.assertIn('self._check_all_btn.pack(side="right"', seg)
        self.assertIn("self._check_all_btn.pack_forget()", seg)

    def test_f41_hover_language(self):
        up_src = open(__import__("os").path.join(
            __import__("os").path.dirname(__import__("os").path.
                       abspath(__file__)), "..", "panel_ui.py")).read()
        self.assertIn("hover_color=accent_hover", up_src)
        self.assertIn('# F41: accent-family hovers on EVERY kind',
                      up_src)

    def test_f41_push_protocol_spec_in_the_store_docstring(self):
        up = _src("user_profile.py")
        self.assertIn("github_authors: [{username, email, token?}]", up)
        self.assertIn("CLONED from one of these accounts keeps that", up)
        self.assertIn("ACCESS TOKEN", up)

    def test_f38_jump_buttons(self):
        # both pages get one-tap travel; sane wiring below
        self.assertEqual(self.src.count("self._add_jump_buttons("), 2)
        self.assertIn('self._add_jump_buttons(body_host, self._canvas,'
                      ' "keys")', self.src)
        self.assertIn('self._add_jump_buttons(host, canvas, "profile")',
                      self.src)
        self.assertIn("def _fly_scroll(self, canvas, target):", self.src)
        self.assertIn('text="\u2191 Top"', self.src.replace(
            "↑", "\u2191"))
        self.assertIn('text="\u2193 Bottom"', self.src.replace(
            "↓", "\u2193"))
        # bottom target derives from the LIVE scrollregion, never a
        # blind 1.0 (yview clamps, but a computed target lands exactly)
        self.assertIn('canvas.bbox("all")', self.src)

    def test_avatar_and_save_wiring(self):
        self.assertIn("user_profile.avatar_photoimage(", self.src)
        self.assertIn("user_profile.save(profile)", self.src)
        self.assertIn("_view.refresh_presence()", self.src)

    def test_cred_rows_masked_and_single_arg_delete(self):
        self.assertIn("user_profile.mask(", self.src)
        self.assertIn("def _del_cred(self, frame):", self.src)
        self.assertNotIn("_del_cred(frame, row_frame)", self.src)


class TestCrossFileSeamPins(unittest.TestCase):
    def test_app_panels_redirect(self):
        src = _src("app_panels.py")
        marker = 'if key == "profile":'
        self.assertIn(marker, src)
        seg = src[src.index(marker):src.index(marker) + 1200]
        self.assertIn('self.open("settings")', seg)
        self.assertIn(".show_page(\"profile\")", seg)

    def test_prompt_injection(self):
        src = _src("agent_overlay.py")
        self.assertIn("user_profile.prompt_summary()", src)
        idx = src.index("user_profile.prompt_summary()")
        self.assertLess(src.rindex("def _system_prompt(self):", 0, idx),
                        idx)

    def test_view_badge(self):
        src = _src(os.path.join("friday_chat", "view.py"))
        self.assertIn("_PRESENCE_VIEWS = []", src)
        self.assertIn("def refresh_presence():", src)
        self.assertIn("def refresh_presence(self):", src)
        self.assertIn("self._presence_badge", src)
        # the badge decodes a 26px avatar through the interpreter-safe seam
        self.assertIn("avatar_photoimage(", src)
        self.assertIn("size=26", src)
        self.assertIn("master=self", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
