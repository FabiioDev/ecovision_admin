"""Copy one Firestore database onto another (exact mirror), for the double write.

The backend already resyncs on its own when a database comes back empty. This
script is the manual path: the initial copy before enabling the replica, or a
repair. It never touches the source.

Usage (from the ecovision_admin root, with ADC credentials):
    python scripts/resync_firestore.py --project ecovision-ai-model \
        --source "(default)" --target ecovision-replica          # shows counts only
    python scripts/resync_firestore.py ... --apply               # writes the target
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from google.cloud import firestore  # noqa: E402

from app.services.replication import PENDING, _walk, resync  # noqa: E402


def count(db: firestore.Client) -> int:
    return sum(1 for c in db.collections() if c.id != PENDING for _ in _walk(c, {PENDING}))


def main() -> int:
    p = argparse.ArgumentParser(description="Mirror one Firestore database onto another")
    p.add_argument("--project", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--apply", action="store_true", help="write the target (default: dry run)")
    a = p.parse_args()
    if a.source == a.target:
        print("source and target must differ")
        return 2
    src = firestore.Client(project=a.project, database=a.source)
    dst = firestore.Client(project=a.project, database=a.target)
    print(f"source {a.source}: {count(src)} documents | target {a.target}: {count(dst)} documents")
    if not a.apply:
        print("dry run: add --apply to copy")
        return 0
    copied = resync(src, dst)
    print(f"copied {copied} documents; target now has {count(dst)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
