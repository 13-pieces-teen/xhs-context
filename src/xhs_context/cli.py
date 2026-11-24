from __future__ import annotations

import argparse
import importlib.metadata
import json
import sqlite3
import sys
from pathlib import Path

from .common import XhsError, dumps, envelope, error_envelope, library_path
from .storage import Store


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--library", default=argparse.SUPPRESS, help="Library path, or XHS_CONTEXT_HOME")
    common.add_argument("--format", choices=["json"], default=argparse.SUPPRESS)
    root = argparse.ArgumentParser(prog="xhs-context", parents=[common], description="Local XHS evidence archive")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ["init", "doctor", "export", "reindex"]:
        commands.add_parser(name, parents=[common])
    setup = commands.add_parser("setup", parents=[common], help="One-time browser integration on this Windows account")
    setup.add_argument("--extension-dir", default=str(Path(__file__).resolve().parents[2] / "browser-extension"))
    status = commands.add_parser("status", parents=[common])
    status.add_argument("--job")
    invalid = commands.add_parser('invalid', parents=[common], help='Read the persistent invalid-note list; never retry sources')
    invalid.add_argument('action', choices=['list'])
    invalid.add_argument('--limit', type=int, default=50)
    invalid.add_argument('--offset', type=int, default=0)
    search = commands.add_parser("search", parents=[common])
    search.add_argument("--query", action="append", help="Repeat for up to four keyword queries")
    search.add_argument("--content", choices=["body", "all"], help="Default: saved text beyond hashtags or decoration; all includes incomplete leads")
    search.add_argument("--require", dest="required_terms", action="append", help="Required name or topic; aliases accepted")
    search.add_argument("--match", choices=["terms", "phrase"], help="phrase requires a complete query in one field and line, excluding hashtags")
    search.add_argument("--collection", action="append")
    search.add_argument("--limit", type=int)
    search.add_argument("--max-chars", type=int)
    search.add_argument("--input")
    search.add_argument("--cursor")
    read = commands.add_parser("read", parents=[common])
    read.add_argument("--input", required=True)
    aliases = commands.add_parser("aliases", parents=[common],
                                  help="List or edit equivalent names used by search (library-wide)")
    aliases.add_argument("action", choices=["list", "add", "remove"])
    aliases.add_argument("names", nargs="*", help="add: two or more equivalent names; remove: one custom name")
    images = commands.add_parser('images', parents=[common], help='Acquire images independently; search/read never fetch images')
    image_sub = images.add_subparsers(dest='images_action', required=True)
    plan = image_sub.add_parser('plan', parents=[common], help='Inspect local image gaps and body references; never acquire')
    plan.add_argument('--note', action='append', dest='note_ids')
    plan.add_argument('--collection', dest='collection_id')
    plan.add_argument('--limit', type=int)
    plan.add_argument('--max-chars', type=int)
    plan.add_argument('--cursor')
    plan.add_argument('--input')
    fetch = image_sub.add_parser('fetch', parents=[common])
    fetch.add_argument('--note', action='append', dest='note_ids')
    fetch.add_argument('--collection', dest='collection_id')
    fetch.add_argument('--input')
    fetch.add_argument('--background', action='store_true', help='Queue only; browser host or images process runs the job')
    for name in ('status', 'pause', 'resume', 'process'):
        command = image_sub.add_parser(name, parents=[common])
        command.add_argument('--job', required=name in {'pause', 'resume'})
    ingest = commands.add_parser("ingest", parents=[common], help="Explicit local snapshot import; not proof of live sync")
    ingest.add_argument("--input", required=True)
    ingest.add_argument("--no-ocr", action="store_true")
    backup = commands.add_parser("backup", parents=[common])
    backup.add_argument("--destination", required=True)
    restore = commands.add_parser("restore", parents=[common])
    restore.add_argument("--source", required=True)
    note = commands.add_parser("note", parents=[common])
    note.add_argument("action", choices=["delete", "restore"])
    note.add_argument("note_id")
    sync = commands.add_parser("sync", parents=[common])
    sub = sync.add_subparsers(dest="sync_action", required=True)
    serve = sub.add_parser("serve", parents=[common])
    serve.add_argument("--port", type=int, default=4319)
    serve.add_argument("--interval", type=float, default=2)
    serve.add_argument("--no-ocr", action="store_true")
    process = sub.add_parser("process", parents=[common])
    process.add_argument("--job")
    process.add_argument("--no-ocr", action="store_true")
    for name in ["pause", "resume"]:
        command = sub.add_parser(name, parents=[common])
        command.add_argument("--job", required=True)
    abandon = sub.add_parser("abandon", parents=[common],
                             help="Close an unfinished list scan as interrupted; memberships are never removed")
    abandon.add_argument("--job", required=True)
    unlock = sub.add_parser("unlock", parents=[common], help="Clear the local restriction guard without resuming jobs")
    unlock.add_argument("--confirm-access-restored", action="store_true")
    return root


def read_input(value):
    if value == "-":
        content = sys.stdin.read(2_000_001)
    else:
        path = Path(value)
        if path.stat().st_size > 2_000_000:
            raise XhsError("INVALID_ARGUMENT", "Request JSON exceeds 2 MB", exit_code=2)
        content = path.read_text(encoding="utf-8-sig")
    if len(content) > 2_000_000:
        raise XhsError("INVALID_ARGUMENT", "Request JSON exceeds 2 MB", exit_code=2)
    try:
        return json.loads(content)
    except ValueError:
        raise XhsError("INVALID_ARGUMENT", "Invalid request JSON", exit_code=2) from None


def doctor(store):
    from .processing import ocr_models, ocr_ready
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        fts = True
    except sqlite3.Error:
        fts = False
    finally:
        db.close()
    versions = {}
    for package in ["xhs-context", "jieba", "rapidocr", "onnxruntime", "Pillow"]:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return envelope({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                     "fts5": fts, "packages": versions, "library": str(store.root),
                     "library_initialized": store.db_path.exists(), "ocr_models_ready": ocr_ready(),
                     "ocr_models": {k: {"path": str(p), "exists": p.exists()} for k, p in ocr_models().items()}},
                    partial=not fts)


def dispatch(args):
    store = Store(library_path(getattr(args, "library", None)))
    if args.command == "init":
        return envelope(store.initialize())
    if args.command == "doctor":
        return doctor(store)
    if args.command == "setup":
        from .setup import install_host
        return envelope(install_host(args.extension_dir, getattr(args, "library", None)))
    if args.command == "status":
        from .core import status
        return status(store, args.job)
    if args.command == 'invalid':
        from .invalid_sources import invalid_report
        with store.read() as db:
            return envelope(invalid_report(db, limit=args.limit, offset=args.offset))
    if args.command == "search":
        from .core import search
        if args.input:
            if any(getattr(args, key) is not None for key in ["query", "collection", "limit", "max_chars", "cursor", "content", "required_terms", "match"]):
                raise XhsError("INVALID_ARGUMENT", "Do not mix --input with search shortcut flags", exit_code=2)
            request = read_input(args.input)
        elif args.cursor:
            if args.query or args.collection or args.limit is not None or args.content is not None or args.required_terms is not None or args.match is not None:
                raise XhsError("INVALID_ARGUMENT", "Use --cursor with --max-chars only", exit_code=2)
            request = {"cursor": args.cursor}
            if args.max_chars is not None:
                request["max_chars"] = args.max_chars
        else:
            request = {"queries": args.query} if args.query is not None else {}
            for key in ("content", "required_terms", "match"):
                if getattr(args, key) is not None:
                    request[key] = getattr(args, key)
            if args.collection is not None:
                request["collection_ids"] = args.collection
            if args.limit is not None:
                request["limit"] = args.limit
            if args.max_chars is not None:
                request["max_chars"] = args.max_chars
        return search(store, request)
    if args.command == "read":
        from .core import read
        return read(store, read_input(args.input))
    if args.command == "aliases":
        from .aliases import add_aliases, list_aliases, remove_alias
        if args.action == "list":
            if args.names:
                raise XhsError("INVALID_ARGUMENT", "aliases list takes no names", exit_code=2)
            return envelope(list_aliases(store))
        if args.action == "add":
            return envelope(add_aliases(store, args.names))
        if len(args.names) != 1:
            raise XhsError("INVALID_ARGUMENT", "aliases remove takes exactly one name", exit_code=2)
        return envelope(remove_alias(store, args.names[0]))
    if args.command == 'images':
        return dispatch_images(store, args)
    if args.command == "ingest":
        from .ingest import ingest_file
        result = ingest_file(store, args.input, ocr=not args.no_ocr)
        return envelope(result, partial=result["partial"])
    if args.command == "export":
        from .exports import export_snapshot
        return envelope(export_snapshot(store))
    if args.command == "backup":
        from .exports import backup
        return envelope(backup(store, args.destination))
    if args.command == "restore":
        from .exports import restore
        return envelope(restore(args.source, store.root))
    if args.command == "reindex":
        return envelope(store.reindex())
    if args.command == "note":
        return envelope(store.set_deleted(args.note_id, args.action == "delete"))
    if args.command == "sync":
        if args.sync_action == "unlock":
            from .access import unlock_access
            return envelope(unlock_access(store, confirmed=args.confirm_access_restored))
        if args.sync_action == "abandon":
            from .jobs import abandon_scan
            return envelope(abandon_scan(store, args.job))
        if args.sync_action in {"pause", "resume"}:
            from .jobs import set_job_state
            return envelope(set_job_state(store, args.job, "paused" if args.sync_action == "pause" else "active",
                                          retry_failed=args.sync_action == "resume"))
        if args.sync_action == "serve":
            from .server import serve
            if not 0 <= args.port <= 65535 or not .1 <= args.interval <= 60:
                raise XhsError("INVALID_ARGUMENT", "Invalid port or interval", exit_code=2)
            serve(store, args.port, ocr=not args.no_ocr, interval=args.interval)
            return None
        from .worker import Worker, sync_lock
        with sync_lock(store):
            worker = Worker(store, ocr=not args.no_ocr)
            worker.recover()
            processed = 0
            while worker.once(args.job):
                processed += 1
        return envelope({"processed": processed})


def dispatch_images(store, args):
    if args.images_action == 'plan':
        from .images import image_plan
        shortcuts = ('note_ids', 'collection_id', 'limit', 'max_chars', 'cursor')
        if args.input:
            if any(getattr(args, key) is not None for key in shortcuts):
                raise XhsError('INVALID_ARGUMENT', 'Do not mix --input with image plan shortcut flags', exit_code=2)
            request = read_input(args.input)
        else:
            request = {key: getattr(args, key) for key in shortcuts if getattr(args, key) is not None}
        return image_plan(store, request)
    from .image_jobs import create_image_job, image_report, image_reports, set_image_state
    from .images import ImageWorker
    from .worker import sync_lock
    if args.images_action == 'status':
        with store.read() as db:
            return envelope(image_report(db, args.job, store=store) if args.job else {'jobs': image_reports(db, store=store)})
    if args.images_action in {'pause', 'resume'}:
        return envelope(set_image_state(store, args.job, 'paused' if args.images_action == 'pause' else 'active'))
    job_id = getattr(args, 'job', None)
    if args.images_action == 'fetch':
        if args.input:
            if args.note_ids or args.collection_id:
                raise XhsError('INVALID_ARGUMENT', 'Do not mix --input with image shortcut flags', exit_code=2)
            request = read_input(args.input)
        else:
            request = {k: getattr(args, k) for k in ('note_ids', 'collection_id') if getattr(args, k) is not None}
        report = create_image_job(store, request)
        job_id = report['job_id']
        if args.background:
            return envelope({'job': report, 'queued': True})
    try:
        with sync_lock(store):
            worker = ImageWorker(store)
            worker.recover()
            while worker.once(job_id):
                pass
    except XhsError as exc:
        if exc.code != 'SYNC_ALREADY_RUNNING':
            raise
        with store.read() as db:
            return envelope({'job': image_report(db, job_id, store=store) if job_id else None, 'queued': True,
                             'processor': 'existing_host', 'message': 'The running host will process queued image jobs'})
    with store.read() as db:
        reports = [image_report(db, job_id, store=store)] if job_id else image_reports(db, store=store)
    return envelope({'jobs': reports}, partial=any(r['failures'] for r in reports))


def main(argv=None):
    for stream in [sys.stdin, sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        if result is not None:
            print(dumps(result))
            return 3 if result.get("partial") else 0
        return 0
    except XhsError as exc:
        print(dumps(error_envelope(exc)))
        return exc.exit_code
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(dumps(error_envelope(XhsError("LOCAL_OPERATION_FAILED", str(exc)[:300], exit_code=5))))
        return 5
    except KeyboardInterrupt:
        print(dumps(error_envelope(XhsError("INTERRUPTED", "Operation interrupted", exit_code=5))))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())

