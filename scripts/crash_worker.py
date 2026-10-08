"""Private subprocess entry point for synchronized crash tests, never an API route."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import Event, Operations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--phase', choices=['before_commit', 'after_commit'], required=True)
    parser.add_argument('--ready', required=True)
    parser.add_argument('--events', required=True)
    args = parser.parse_args()
    os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(args.database).resolve()).replace('\\', '/')

    def pause():
        Path(args.ready).write_text(args.phase)
        # Parent waits for ready before forcibly terminating this process.
        while True:
            time.sleep(0.05)

    service = Operations(test_hooks={args.phase: pause})
    service.restore()
    service.persist_events([Event.model_validate(e) for e in json.loads(Path(args.events).read_text())])


if __name__ == '__main__':
    main()
