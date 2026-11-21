"""Run the monitor.

Live, next to the simulator's clock (started after the backfill, before `python -m sim.live`):

    python -m brain.run --fresh

With --narrate the live loop also asks a language model for a short narration of each incident,
written from the incident's own evidence and checked against it (brain/narrate.py; needs an API key
with credit, falls back to nothing but the template report otherwise).

Replay of a finished database hour by hour, as if it were live (for the scoring harness):

    python -m brain.run --replay --db data/eval.db --incidents eval_incidents --fresh

Paths default to the environment variables the console uses (MARKET_DB, INCIDENTS_DIR, GRAPH_JSON),
then to data/market.db, incidents/ and no graph export. Stop it with Ctrl+C before a database is
rebuilt: on Windows an open reader blocks deleting the file.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

from brain.clock import HOUR, Clock
from brain.data import Store, iso_hour
from brain.extension import Extension
from brain.graph import MetricGraph
from brain.incidents import Book, write_json
from brain.monitor import Config, Monitor
from brain.narrate import Narrator, describe as describe_narrator


def export_graph(graph: MetricGraph, store: Store, path: Path) -> None:
    """Validate the graph on the data, then publish it where the console reads it."""
    from brain.validate import summarize, validate_graph
    print(summarize(validate_graph(graph, store, days=7)))
    write_json(path, graph.to_dict())
    print(f"graph written to {path}")


def describe(tick, written) -> str:
    line = f"{iso_hour(tick.newest)}  alerts {len(tick.alerts):3d}"
    if tick.held:
        line += f"  held {len(tick.held)} h"
    for body in written:
        seg = " x ".join(body["segment"].values()) or "all"
        driver = body["drivers"][0]["metric"]
        line += f"\n    {body['id']} {body['status']:8s} {driver} in {seg}"
        if body["hypotheses"] and body["hypotheses"][0]["status"] == "supported":
            line += f"  <- {body['hypotheses'][0]['cause']}"
    return line


def replay(monitor: Monitor, book: Book, start=None, end=None, quiet: bool = False) -> int:
    first, last = monitor.store.time_range()
    cfg = monitor.config
    t = pd.Timestamp(start) if start else first + (cfg.min_history_hours + cfg.reread_hours) * HOUR
    stop = pd.Timestamp(end) if end else last + HOUR
    ticks, began = 0, time.time()
    while t <= stop:
        tick = monitor.tick(as_of=t)
        written = book.update(tick)
        if written and not quiet:
            print(describe(tick, written), flush=True)
        t += HOUR
        ticks += 1
    opened = len(book.incidents)
    print(f"replayed {ticks} hours in {time.time() - began:.1f} s: {opened} incident(s) in {book.folder}")
    return opened


def live(monitor: Monitor, book: Book, poll: float, max_ticks: int | None = None, quiet: bool = False,
         extension: Extension | None = None) -> None:
    last_seen, idle, ticks = None, 0, 0
    print("monitor is watching; Ctrl+C stops it", flush=True)
    while max_ticks is None or ticks < max_ticks:
        try:
            if extension is not None:
                try:
                    extension.poll()   # new metric definitions in the incoming folder; it answers them itself
                except Exception:      # whatever happens there must not cost the monitor its tick
                    traceback.print_exc()
            newest = Clock(monitor.store).newest_hour()
            if newest is not None and (newest != last_seen or idle >= 4):   # also re-read when nothing new came
                tick = monitor.tick()
                written = book.update(tick)
                if not quiet and (written or newest != last_seen):
                    print(describe(tick, written), flush=True)
                last_seen, idle = newest, 0
                ticks += 1
            else:
                idle += 1
        except Exception:   # a locked file or a busy database must not end the monitor: say so and try again
            traceback.print_exc()
            print("tick failed; trying again", file=sys.stderr, flush=True)
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("MARKET_DB", "data/market.db"))
    ap.add_argument("--incidents", default=os.environ.get("INCIDENTS_DIR", "incidents"))
    ap.add_argument("--graph-out", default=os.environ.get("GRAPH_JSON"),
                    help="validate the graph and write its export here (live mode)")
    ap.add_argument("--incoming", default=os.environ.get("INCOMING_METRICS_DIR", "incoming_metrics"),
                    help="live: folder watched for new metric definitions (*.yaml); the answers are written next to "
                         "them, and --fresh removes the old answers there (the YAML files stay)")
    ap.add_argument("--fresh", action="store_true", help="start with an empty incidents folder")
    ap.add_argument("--replay", action="store_true", help="walk the whole database hour by hour, then stop")
    ap.add_argument("--from", dest="start", help="replay: first clock value (default: after the minimum history)")
    ap.add_argument("--to", dest="end", help="replay: last clock value (default: one hour after the newest data)")
    ap.add_argument("--poll", type=float, default=0.5, help="live: seconds between looks at the database")
    ap.add_argument("--max-ticks", type=int, help="live: stop after this many ticks (for tests)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--narrate", action="store_true",
                    help="live: a language model narrates each incident from its evidence (brain/narrate.py)")
    a = ap.parse_args(argv)

    store = Store(a.db)
    graph = MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    narrator = Narrator() if a.narrate and not a.replay else None
    if a.narrate and a.replay:
        print("narration is for the live loop; the replay runs without it")
    book = Book(monitor, a.incidents, fresh=a.fresh, narrator=narrator)
    try:
        if a.replay:
            replay(monitor, book, a.start, a.end, a.quiet)
        else:
            if a.graph_out:
                export_graph(graph, store, Path(a.graph_out))
            extension = Extension(graph, store, a.incoming, a.graph_out, monitor, fresh=a.fresh, quiet=a.quiet)
            if a.narrate:
                print(describe_narrator(narrator), flush=True)
            live(monitor, book, a.poll, a.max_ticks, a.quiet, extension)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if narrator is not None:
            narrator.close()
        store.conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
