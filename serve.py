"""
serve.py — run Project Estimation for the Project Enquiry tool.

The estimator keeps its own modules (it has a calculator.py and a database.py of
its own, as does the enquiry tool), so it runs as its own process with its own
folder first on the path. The enquiry tool's folder comes after it, which is how
vendors.py finds the global vendor list and inventory.py finds the Store.

No window and no browser tab: the enquiry tool shows this inside its own window.
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                       # the estimator's own modules win
sys.path.append(os.path.dirname(HERE))         # then the enquiry tool: accounts, store
sys.dont_write_bytecode = True

import app as estimator                        # noqa: E402

logging.getLogger("werkzeug").setLevel(logging.WARNING)

if __name__ == "__main__":
    port = int(os.environ.get("EST_PORT", "5071"))
    print(f"Project Estimation on http://localhost:{port}", flush=True)
    estimator.app.run(host="localhost", port=port, threaded=True,
                      use_reloader=False, debug=False)
