"""Real IDE fork Commit & Push flow: new branch, push, PR body."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDE_DIR = os.path.join(ROOT, "ide")
for path in (ROOT, IDE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    import memory
    import user_profile
    import git_bridge
    import ide as ide_mod
except Exception as exc:
    memory = user_profile = git_bridge = ide_mod = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@unittest.skipIf(ide_mod is None, "IDE GUI dependencies unavailable: %s" %
                 _IMPORT_ERROR)
class ForkCommitPushFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fork_pr_flow_")
        self.mem = os.path.join(self.tmp, "profile")
        memory.set_memory_dir(self.mem)
        profile = user_profile.default_profile()
        profile["github_authors"] = [{
            "username": "veloura-dev", "email": "veloura@example.test",
            "token": "ghp_TEST_author_token"}]
        profile["credentials"] = []
        user_profile.save(profile)

        self.repo = os.path.join(self.tmp, "fork")
        self.bare = os.path.join(self.tmp, "origin.git")
        os.makedirs(self.repo)
        git(self.tmp, "init", "--bare", self.bare)
        git(self.repo, "init")
        git(self.repo, "config", "user.name", "Initial")
        git(self.repo, "config", "user.email", "initial@example.test")
        git(self.repo, "checkout", "-b", "main")
        with open(os.path.join(self.repo, "README.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("seed\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "seed")
        git(self.repo, "remote", "add", "origin", self.bare)
        git(self.repo, "push", "-u", "origin", "main")

        self.app = ide_mod.IDEApp(self.repo, show_chat=False)
        self.app.update_idletasks()
        self.app.update()
        self.app.withdraw()
        with open(os.path.join(self.repo, "change.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("feature change\n")

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass

    def _run_flow(self, fork):
        result = {}
        prompts = iter(["Add feature", "feature/add-feature",
                        "Add feature PR"])
        self.app._prompt_text = lambda *a, **k: next(prompts)
        self.app._prompt_multiline = lambda *a, **k: "Why this change matters."
        self.app._git_report = lambda title, res: result.update(
            title=title, result=res)
        self.app._run_git_op = lambda title, work: result.update(
            title=title, result=work())
        child = {"ok": True, "fork": fork,
                 "full_name": "veloura-dev/fork",
                 "parent_full_name": "upstream/project" if fork else "",
                 "default_branch": "main"}
        parent = {"ok": True, "full_name": "upstream/project",
                  "default_branch": "main"}
        repo_info = mock.Mock(side_effect=lambda owner, repo, **kw:
                               parent if owner == "upstream" else child)
        create_pull = mock.Mock(return_value={
            "ok": True, "html_url": "https://github.com/upstream/project/pull/2",
            "number": 2})
        with mock.patch.object(git_bridge, "remote_url",
                               return_value="https://github.com/veloura-dev/fork.git"), \
             mock.patch.object(git_bridge, "repo_info", repo_info), \
             mock.patch.object(git_bridge, "create_pull", create_pull), \
             mock.patch.object(ide_mod.messagebox, "askyesno", return_value=True):
            self.app.github_commit_dialog(then_push=True)
            deadline = time.time() + 5
            while time.time() < deadline and not result:
                self.app.update_idletasks()
                self.app.update()
                time.sleep(0.02)
        return result, repo_info, create_pull

    def test_fork_creates_branch_pushes_and_opens_pr_with_description(self):
        result, repo_info, create_pull = self._run_flow(fork=True)
        self.assertEqual(result.get("title"), "Commit, Push & Open PR")
        self.assertTrue(result["result"]["ok"], result["result"])
        self.assertEqual(git(self.repo, "branch", "--show-current"),
                         "feature/add-feature")
        branches = git(self.bare, "for-each-ref", "--format=%(refname:short)",
                       "refs/heads").splitlines()
        self.assertIn("feature/add-feature", branches)
        create_pull.assert_called_once_with(
            "upstream/project", "main", "veloura-dev:feature/add-feature",
            "Add feature PR", body="Why this change matters.",
            token="ghp_TEST_author_token")
        self.assertEqual(repo_info.call_count, 2)

    def test_nonfork_keeps_standard_commit_and_push(self):
        result, _, create_pull = self._run_flow(fork=False)
        self.assertEqual(result.get("title"), "Commit & Push")
        self.assertTrue(result["result"]["ok"], result["result"])
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        create_pull.assert_not_called()


if __name__ == "__main__":
    unittest.main()
