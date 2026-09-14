import tempfile, unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from dispatch import render

class DispatchTests(unittest.TestCase):
    def test_dispatch_renders_action_and_requires_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            db=ControlDB(Path(d)/"run.db"); db.create_run("r","i","q")
            result=render(db,"r")
            self.assertEqual("final_release",result["action"]["kind"])
            self.assertTrue(result["receipt_required"])
            db.close()

if __name__ == "__main__": unittest.main()
