import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("transitions.py")
LABELS = ("correct", "incorrect", "missing", "uncertain")

def fixture(mode):
    p = dict(mode=mode, intended_action="candidate_only", selection="all synthetic cells",
             coverage_scope="synthetic fixture", clock="last published value at frozen checkpoints",
             decision_rule="preview only", snapshots=["before", "after"], arms=["baseline", "guard"],
             targets=[])
    rows = []
    for i, a in enumerate(LABELS):
        for j, b in enumerate(LABELS):
            ident = f"{i}-{j}"
            p["targets"].append(dict(id=ident, cluster="lesson", reference_anchor="synthetic",
                                     proposition="The unit is seconds.", reference_sufficient=True))
            for arm in p["arms"]:
                for s, val in zip(p["snapshots"], (a, b)):
                    rows.append(dict(id=ident, arm=arm, snapshot=s, label=val, evidence="fixture"))
    return p, rows

class TestCli(unittest.TestCase):
    def run_case(self, plan, rows, sha=None):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "plan.json"
            f = Path(tmp) / "labels.json"
            p.write_text(json.dumps(plan))
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            f.write_text(json.dumps(dict(plan_sha256=h, observations=list(reversed(rows)))))
            return subprocess.run([sys.executable, str(SCRIPT), str(p), str(f),
                                   "--plan-sha256", sha or h], capture_output=True, text=True)

    def test_both_modes_all_transitions(self):
        for mode in ("live_zh", "offline_en"):
            p, rows = fixture(mode)
            run = self.run_case(p, rows)
            self.assertEqual(run.returncode, 0, run.stderr)
            for arm in json.loads(run.stdout)["results"].values():
                self.assertTrue(all(v == 1 for r in arm["matrix"].values() for v in r.values()))
                self.assertEqual([arm[k] for k in ("new_corrections","regressions",
                    "new_omissions","recovered_omissions","rateable_pairs","uncertain_pairs")],
                    [1,1,1,1,9,7])

    def test_missing_observation_and_common_subset(self):
        p, rows = fixture("live_zh")
        res = json.loads(self.run_case(p, rows[1:]).stdout)["results"]
        self.assertEqual(res["baseline"]["incomplete_pairs"], 1)
        self.assertEqual(res["baseline"]["observed"]["before"]["missing"], 4)
        self.assertEqual(res["guard"]["common_rateable_pairs"], 8)

    def test_bad_inputs(self):
        p, rows = fixture("offline_en")
        for change in (dict(id="unknown"), dict(snapshot="late"), dict(label="guess"), dict(evidence="")):
            self.assertEqual(self.run_case(p, [dict(rows[0], **change)]).returncode, 2)
        self.assertEqual(self.run_case(p, rows + rows[:1]).returncode, 2)
        self.assertEqual(self.run_case(p, rows, sha="wrong").returncode, 2)
        self.assertEqual(self.run_case(dict(p, intended_action=""), rows).returncode, 2)
        p["targets"][0]["reference_sufficient"] = False
        self.assertEqual(self.run_case(p, rows).returncode, 2)

if __name__ == "__main__":
    unittest.main()
