"""Settings -> Profile layout: Profile is the first tab and the page the
panel opens on; the photo header sits at the very top of the page; the
Save bar is pinned outside the scroll area; photo preview/clear and the
live header work; saving still round-trips every field.

Runs a REAL SettingsPanel against a temp memory folder (needs a display).
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import memory  # noqa: E402
import user_profile  # noqa: E402


def _png(path, color=(200, 80, 40)):
    from PIL import Image
    Image.new("RGB", (64, 64), color).save(path)
    return path


class TestProfileFirstLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:
            raise unittest.SkipTest("no display")

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="profile_layout_")
        self.prev = memory._memory_dir
        memory.set_memory_dir(self.tmp)
        self.photo = _png(os.path.join(self.tmp, "me.png"))
        user_profile.save({**user_profile.default_profile(),
                           "name": "Ada Lovelace", "dev_names": "ada, alove",
                           "bio": "Analyst.", "photo": self.photo,
                           "links": {**user_profile.default_profile()["links"],
                                     "github": "https://github.com/ada"}})
        self.panels = []

    def tearDown(self):
        for p in self.panels:
            try:
                p.destroy()
            except Exception:  # noqa: BLE001
                pass
        memory.set_memory_dir(self.prev)

    def _panel(self, **kw):
        import settings_panel
        p = settings_panel.SettingsPanel(
            self.root, on_minimize=lambda: None, on_close=lambda: None,
            corner_index=0, initial_content=None, bounds=None, **kw)
        self.panels.append(p)
        for _ in range(20):
            self.root.update()
        return p

    def _press_return(self, entry):
        # key events go to the focused widget, so focus the field first
        entry._entry.focus_force()
        self.root.update()
        entry._entry.event_generate("<Return>")

    def test_profile_is_first_tab_and_default_page(self):
        p = self._panel()
        self.assertEqual(list(p._page_keys), ["Profile", "Keys & Connect"])
        self.assertEqual(p._page, "profile")
        self.assertEqual(p._seg.get(), "Profile")
        self.assertTrue(p._profile_host.winfo_ismapped())
        self.assertFalse(p._keys_host.winfo_ismapped())
        # Check all belongs to the keys page only
        self.assertFalse(p._check_all_btn.winfo_ismapped())

    def test_keys_page_still_reachable(self):
        p = self._panel(initial_page="keys")
        self.assertEqual(p._page, "keys")
        self.assertTrue(p._check_all_btn.winfo_ismapped())
        p.show_page("profile")
        self.root.update()
        self.assertTrue(p._profile_host.winfo_ismapped())
        self.assertIn("profile", p._subheader_lbl.cget("text").lower())
        p.show_page("keys")
        self.assertIn("Keys are masked", p._subheader_lbl.cget("text"))

    def test_photo_header_is_at_the_top(self):
        p = self._panel()
        av_y = p._avatar_canvas.winfo_rooty()
        # above every field on the page
        for ent in list(p._profile_vars.values()) + list(p._link_vars.values()):
            self.assertLess(av_y, ent.winfo_rooty())
        self.assertIsNotNone(p._photo_img, "saved photo should load")
        self.assertEqual(p._hdr_name.cget("text"), "Ada Lovelace")
        self.assertIn("@ada", p._hdr_handles.cget("text"))
        self.assertIn("@alove", p._hdr_handles.cget("text"))

    def test_save_bar_is_pinned_outside_the_scroll_area(self):
        p = self._panel()
        status = p._profile_status
        self.assertTrue(status.winfo_ismapped())
        # not inside the scrolling canvas
        w = status
        while w is not None and w is not p._profile_canvas:
            w = w.master
        self.assertIsNone(w)

    def test_live_header_and_photo_clear_and_preview(self):
        p = self._panel()
        name = p._profile_vars["name"]
        name.delete(0, "end")
        name.insert(0, "Grace Hopper")
        p._refresh_profile_header()
        self.assertEqual(p._hdr_name.cget("text"), "Grace Hopper")
        p._clear_photo()
        self.assertIsNone(p._photo_img)
        self.assertEqual(p._photo_entry.get(), "")
        texts = [p._avatar_canvas.itemcget(i, "text")
                 for i in p._avatar_canvas.find_all()
                 if p._avatar_canvas.type(i) == "text"]
        self.assertIn("GH", texts)      # initials placeholder
        p._photo_entry.insert(0, self.photo)
        self._press_return(p._photo_entry)
        self.root.update()
        self.assertIsNotNone(p._photo_img)
        p._photo_entry.delete(0, "end")
        p._photo_entry.insert(0, os.path.join(self.tmp, "nope.png"))
        self._press_return(p._photo_entry)
        self.root.update()
        self.assertIn("Couldn't load", p._profile_status.cget("text"))

    def test_save_round_trip_unchanged(self):
        p = self._panel()
        p._profile_vars["goals"].insert(0, "ship it")
        p._link_vars["website"].insert(0, "https://ada.dev")
        p._apps_entry.insert(0, "VS Code, Figma")
        p._save_profile()
        got = user_profile.load()
        self.assertEqual(got["name"], "Ada Lovelace")
        self.assertEqual(got["bio"], "Analyst.")
        self.assertEqual(got["goals"], "ship it")
        self.assertEqual(got["photo"], self.photo)
        self.assertEqual(got["links"]["website"], "https://ada.dev")
        self.assertEqual(got["links"]["github"], "https://github.com/ada")
        self.assertEqual(got["connected_apps"], ["VS Code", "Figma"])
        self.assertIn("Saved", p._profile_status.cget("text"))

    def test_empty_lists_take_no_space_and_grow_with_rows(self):
        p = self._panel()
        self.assertLess(p._emails_host.winfo_height(), 20)
        p._new_addr.insert(0, "ada@example.com")
        p._new_epass.insert(0, "pw")
        p._email_row(stored=None)
        for _ in range(10):
            self.root.update()
        self.assertGreater(p._emails_host.winfo_height(), 15)
        self.assertEqual(len(p._email_rows), 1)

    def test_completeness_meter(self):
        p = self._panel()
        # name, handles, bio, photo, a link = 5 of 7
        self.assertIn("71%", p._hdr_meter_lbl.cget("text"))
        p._profile_vars["interests"].insert(0, "math")
        p._profile_vars["goals"].insert(0, "engines")
        p._refresh_profile_header()
        self.assertEqual(p._hdr_meter_lbl.cget("text"), "Profile complete")


if __name__ == "__main__":
    unittest.main(verbosity=2)
