"""Regression checks for the evidence used by the build and council gates."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adws"))

from adw_council_sdlc import council_tally
from adw_modules import build_diff, gates
from adw_modules.data_types import BuildOutput, CouncilVoteOutput


class EvidenceTest(unittest.TestCase):
    def test_benched_seats_and_residual_blockers(self):
        yes = CouncilVoteOutput(status="success", approve=True)
        no = CouncilVoteOutput(status="success", approve=False, blocking=["missing assertion"])
        votes = [("one", yes), ("two", no)]
        first = council_tally(votes, False)
        last = council_tally(votes, True)
        self.assertEqual(first["landing_decision"], "red")
        self.assertEqual(last["landing_decision"], "soft")
        self.assertEqual([last[k] for k in ("reviewed","approved","objected","benched")], [2,1,1,4])
        self.assertEqual(last["residual_blockers"], ["[two] missing assertion"])
        self.assertEqual(council_tally([], True)["landing_decision"], "red")
        self.assertEqual(council_tally([("one",yes)],False)["landing_decision"], "green")

    def test_actual_paths_against_first_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root)
            git("init","-q")
            for name in ("prior","remove","mode","old"):
                (root/name).write_text(name)
            git("add",".")
            git("-c","user.name=Test","-c","user.email=test@example.invalid","commit","-qm","base")
            (root/"prior").write_text("staged")
            git("add","prior")
            (root/"prior").write_text("unstaged")
            index = git("write-tree")
            run = SimpleNamespace(repo_root=root, build_change_baseline=build_diff.snapshot(root))
            (root/"remove").unlink()
            (root/"old").rename(root/"new name")
            os.chmod(root/"mode",0o755)
            names = ["remove","old","new name","mode"]
            envelope = BuildOutput(status="success",changed_files=names)
            self.assertTrue(gates.diff_matches_claims(envelope,run).passed)
            (root/"prior").write_text("edited during retry")
            self.assertFalse(gates.diff_matches_claims(envelope,run).passed)
            envelope.changed_files.append("prior")
            self.assertTrue(gates.diff_matches_claims(envelope,run).passed)
            self.assertEqual(git("write-tree"),index)
