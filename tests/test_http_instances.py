# Load the standalone demo script dynamically for the integration test.
import importlib.util
# Represent filesystem paths; the HTTP integration test uses this to locate its demo script.
from pathlib import Path


# Run the multi-process demo and enforce dashboard and stream visibility deadlines.
def test_three_http_process_dashboard_sse_and_restart(tmp_path):
    # Locate the demo script as an importable module without running its CLI entry point.
    spec = importlib.util.spec_from_file_location('round2_demo', Path('scripts/demo.py'))
    # Create the module object described by the import specification.
    module = importlib.util.module_from_spec(spec)
    # Execute the script definitions so run_demo becomes available.
    spec.loader.exec_module(module)
    # Run the demo with three HTTP servers and isolated temporary storage.
    report = module.run_demo(tmp_path / 'http')
    # Require updates to become visible across HTTP processes within five seconds.
    assert report['dashboard_visibility_seconds'] < 5
    # Require updates to become visible across HTTP processes within five seconds.
    assert report['sse_visibility_seconds'] < 5
