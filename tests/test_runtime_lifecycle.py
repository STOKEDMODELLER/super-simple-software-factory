import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "adws"))

from adw_modules.tracer import Tracer
from adw_modules.runtime import admit, release


def test_interrupted_status_is_terminal_and_admission_is_released(tmp_path):
    tracer = Tracer(tmp_path / "trace.db", tmp_path / "events.jsonl")
    tracer.session_start("run-1", "tester")
    admit(tracer, "run-1", 1)
    tracer.session_finish("run-1", ok=False, status="interrupted")
    tracer.session_finish("run-1", ok=False)  # later generic failure cannot overwrite it
    assert tracer.conn.execute("select status from sessions where adw_id='run-1'").fetchone()[0] == "interrupted"
    release(tracer, "run-1")
    admit(tracer, "run-2", 1)
    release(tracer, "run-2")
