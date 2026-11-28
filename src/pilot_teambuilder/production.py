"""Offline hotfolder coordinator; bounded work, durable receipts and safe delivery."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from . import __version__
from .desktop_engine import DesktopEngine
from .discovery import team_folders
from .inventory_watch import merge_inventory, snapshot
from .master_store import install_copy, verify_jpeg
from .review import fingerprint, upgrade_job, validate_job


class SourceNotSettled(ValueError):
    pass


def process_alive(pid):
    if os.name == "nt":
        # Windows os.kill(pid, 0) is not a portable liveness probe. Query only.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID is dead; access failures hold.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class StableFolders:
    def __init__(self):
        self.seen = {}

    def scan(self, root, quiet=20, require_ready=False, now=None):
        now = monotonic() if now is None else now
        if not root.is_dir():
            raise OSError("Incoming hotfolder is unavailable; reconnect it before resuming.")
        folders = team_folders(root)
        result = []
        for folder in folders:
            try:
                version = snapshot(folder)
                if not any(Path(p[0]).suffix.lower() == ".png" for p in version):
                    continue
                key = str(folder.resolve())
                previous = self.seen.get(key)
                if previous is None or previous[0] != version:
                    self.seen[key] = (version, now)
                ready = (not require_ready or (folder / "team.ready").is_file())
                stable = ready and now - self.seen[key][1] >= quiet
                message = "Waiting for team.ready" if not ready else "Waiting for copying to finish"
                result.append((folder, version, stable, "Queued" if stable else message))
            except (OSError, ValueError) as error:
                self.seen.pop(str(folder.resolve()), None)
                result.append((folder, (), False, str(error)))
        live = {str(f.resolve()) for f, *_ in result}
        self.seen = {k: v for k, v in self.seen.items() if k in live}
        return result


class ProductionEngine:
    def __init__(self, state_dir, detector=None):
        state_dir = Path(state_dir).resolve()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.root = state_dir
        self.engine = DesktopEngine(state_dir / "detections.sqlite3", detector)
        self.db = sqlite3.connect(state_dir / "production.sqlite3", timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS teams (key TEXT PRIMARY KEY, state TEXT, output TEXT, receipt TEXT, message TEXT, owner TEXT, pid INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS routes (key TEXT, destination TEXT, state TEXT, PRIMARY KEY(key,destination))")
        self.db.execute("CREATE TABLE IF NOT EXISTS identities (content_key TEXT PRIMARY KEY, ledger_key TEXT NOT NULL)")
        self.db.commit()
        self.owner = uuid4().hex
        self.scanner = StableFolders()
        self.hashes = OrderedDict()
        self.retries = {}

    def close(self):
        self.db.execute("UPDATE teams SET state='interrupted' WHERE owner=? AND state='processing'", (self.owner,))
        self.db.commit()
        self.engine.close()
        self.db.close()

    def _log(self, event, **details):
        with (self.root / "production.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": time(), "event": event, **details}) + "\n")

    def _state(self, key, state, output, receipt=None, message=""):
        self.db.execute("INSERT INTO teams VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET state=excluded.state, output=excluded.output, receipt=excluded.receipt, message=excluded.message, owner=excluded.owner, pid=excluded.pid",
                        (key, state, str(output), json.dumps(receipt) if receipt else None,
                         message, self.owner, os.getpid()))
        self.db.commit()

    def _claim(self, key, output):
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("SELECT state,output,receipt,message,owner,pid FROM teams WHERE key=?", (key,)).fetchone()
        if row and row[0] == "processing" and row[4] != self.owner:
            if process_alive(row[5]):
                self.db.rollback()
                return ("busy", row[1], None, "Another running app is processing this team.")
        if not row:
            if output.exists():
                self.db.rollback()
                raise ValueError("An untracked output already exists; choose a new completed folder.")
            self.db.execute("INSERT INTO teams VALUES (?,?,?,?,?,?,?)", (key, "processing", str(output), None, "", self.owner, os.getpid()))
        elif row[0] not in {"completed", "review", "routing", "busy"}:
            self.db.execute("UPDATE teams SET owner=?,pid=?,state='processing' WHERE key=?", (self.owner, os.getpid(), key))
        self.db.commit()
        return row

    def _deliver(self, key, output, destination, digest):
        target = Path(destination) / output.name
        record = self.db.execute("SELECT state FROM routes WHERE key=? AND destination=?", (key, str(target))).fetchone()
        if record and record[0] == "done":
            return  # A printer may already have consumed it. Never send twice.
        if record and record[0] == "sending":
            if target.is_file() and file_hash(target) == digest:
                self.db.execute("UPDATE routes SET state='done' WHERE key=? AND destination=?", (key, str(target)))
                self.db.commit()
                return
            raise ValueError("Delivery was interrupted and may already have printed. Check the destination before deliberately rebuilding; automatic resend is blocked.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Destination contains an untracked file with this name; no overwrite or duplicate print was attempted.")
        temporary = None
        published = False
        try:
            fd, temporary = tempfile.mkstemp(prefix=".pilot-stage-", suffix=".tmp", dir=target.parent)
            with os.fdopen(fd, "wb") as writer, output.open("rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(block)
                writer.flush()
                os.fsync(writer.fileno())
            if file_hash(temporary) != digest:
                raise OSError("Destination copy verification failed.")
            self.db.execute("INSERT OR REPLACE INTO routes VALUES (?,?,?)", (key, str(target), "sending"))
            self.db.commit()
            os.link(temporary, target)
            published = True
            self.db.execute("UPDATE routes SET state='done' WHERE key=? AND destination=?", (key, str(target)))
            self.db.commit()
        except Exception:
            if not published:
                self.db.execute("DELETE FROM routes WHERE key=? AND destination=?", (key, str(target)))
                self.db.commit()
            raise
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)

    def _source_hash(self, path):
        from .measurements import MeasurementCache
        version = MeasurementCache.signature(path)
        if version not in self.hashes:
            self.hashes[version] = file_hash(path)
            while len(self.hashes) > 4096:
                self.hashes.popitem(last=False)
        self.hashes.move_to_end(version)
        return self.hashes[version]

    def _configuration(self, job, team):
        def version(path):
            try:
                return snapshot(path) if path.is_dir() else (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            except (OSError, ValueError):
                return "unavailable"
        template_root = Path(team.get("templates") or job["templates"])
        files = {str(template_root): version(template_root)}
        for key in ("background", "foreground", "row_csv"):
            if job.get(key):
                files[job[key]] = version(Path(job[key]))
        return fingerprint({
            "version": __version__, "files": files, "manifest": team["manifest"],
            "rows": team.get("requested_rows"), "review_hashes": team.get("review_hashes"),
            "settings": job.get("job_settings"), "scale": job.get("base_scale"),
            "canvas": job.get("capture_canvas"), "retained": job.get("retained_capture", True), "relaxed": job.get("relaxed_detection", True),
            "output": job["output"], "routes": job.get("routes", []),
        })

    def _identity(self, job, folder, version, team):
        included = {e["file"] for e in team["manifest"]["athletes"] if not e.get("excluded", False)}
        image_versions = [p for p in version if p[0] in included]
        version_key = fingerprint((image_versions, team.get("rebuild_nonce", 0)))
        saved = team.get("content_identity", {})
        if saved.get("version") == version_key:
            return saved["key"], False
        hashes = [(p[0], self._source_hash(folder / p[0])) for p in image_versions]
        relative = folder.resolve().relative_to(Path(job["incoming"]).resolve()).as_posix()
        content = fingerprint((str(Path(job["incoming"]).resolve()), relative,
                               sorted(h for _, h in hashes), team.get("rebuild_nonce", 0)))
        found = self.db.execute("SELECT ledger_key FROM identities WHERE content_key=?", (content,)).fetchone()
        key = found[0] if found else content
        if not found:
            # Adopt 0.8 receipts by their original identity; never reprint on upgrade.
            legacy = fingerprint((str(Path(job["incoming"]).resolve()), folder.name, hashes, team.get("rebuild_nonce", 0)))
            if self.db.execute("SELECT 1 FROM teams WHERE key=?", (legacy,)).fetchone():
                key = legacy
            self.db.execute("INSERT OR IGNORE INTO identities VALUES (?,?)", (content, key))
            self.db.commit()
        team["content_identity"] = {"version": version_key, "key": key}
        return key, True

    def _render_master(self, job, team, folder, version, output, cancel):
        current = deepcopy(job)
        current["selected_team"] = job["teams"].index(team)
        view = self.engine.view(current, None, preview=False)
        try:
            if view.analysis.layout is None or not view.templates_ready:
                problems = ([view.template_issue] if view.template_issue else []) + view.analysis.report["issues"]
                problems += [f"{r['file']}: {issue}" for r in view.analysis.report["athletes"] for issue in r["issues"]]
                raise ValueError("; ".join(problems[:4]) or "This team requires attention.")
            if not job.get("capture_canvas"):
                job["capture_canvas"] = view.analysis.report.get("capture_canvas")
            if job.get("base_scale") is None and not job.get("job_settings", {}).get("foreground_framing") and not job.get("job_settings", {}).get("transparency_mode"):
                factor = current.get("job_settings", {}).get("group_scale", 1.0)
                job["base_scale"] = view.analysis.layout.global_scale / factor * 0.85
                current["base_scale"] = job["base_scale"]
                current["capture_canvas"] = job.get("capture_canvas")
                view.close()
                view = self.engine.view(current, None, preview=False)
            if view.analysis.report["issues"]:
                raise ValueError("; ".join(view.analysis.report["issues"][:4]))
            def guard_publication():
                if snapshot(folder) != version or (cancel and cancel.is_set()):
                    raise SourceNotSettled("Team changed or monitoring paused. Waiting before retrying; no output was published.")
            guard_publication()
            if output.exists():
                recovery = self.root / "recovery" / (uuid4().hex + ".jpg")
                try:
                    rendered = self.engine.export(current, view.review_signature, recovery, before_publish=guard_publication, prepared_view=view)
                    if file_hash(recovery) != file_hash(output):
                        raise ValueError("Interrupted output differs from the recovered render. Use Rebuild selected team for the changed layout.")
                finally:
                    recovery.unlink(missing_ok=True)
            else:
                rendered = self.engine.export(current, view.review_signature, output, before_publish=guard_publication, prepared_view=view)
            guard_publication()
            return {"sha256": file_hash(output), "master": str(output),
                    "base_scale": job.get("base_scale"), "capture_canvas": job.get("capture_canvas"),
                    "signature": view.review_signature, "measurement_timings": view.analysis.report["timings"],
                    "render_timings": rendered["timings"], "source_cache_hits": rendered.get("source_cache_hits", 0)}
        finally:
            view.close()

    def _recover_master(self, key, previous, job, team, folder, version, master, published, cancel):
        receipt = json.loads(previous[2]) if previous[2] else {}
        old_hash = receipt.get("sha256")
        if master.exists() and not verify_jpeg(master, old_hash):
            quarantine = self.root / "recovery" / (master.stem + "-retained-" + uuid4().hex + ".jpg")
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(master, quarantine)
        public = receipt.get("published_output")
        if not public:
            old = Path(previous[1])
            public = str(old if not old.is_relative_to(self.root / "Masters") else published)
        candidates = [Path(previous[1]), Path(public), published]
        candidates += [Path(r[0]) for r in self.db.execute("SELECT destination FROM routes WHERE key=? AND state='done'", (key,))]
        recovered = False
        for candidate in dict.fromkeys(candidates):
            if candidate != master and verify_jpeg(candidate, old_hash):
                install_copy(candidate, master, old_hash)
                recovered = True
                break
        if not recovered:
            fresh = self._render_master(job, team, folder, version, master, cancel)
            receipt["delivered_sha256"] = receipt.get("delivered_sha256", old_hash)
            receipt.update(fresh)
            receipt["recovered_from_current_settings"] = True
        receipt.update(master=str(master), published_output=public)
        receipt["sha256"] = file_hash(master)
        self._state(key, "completed", master, receipt)
        self._log("master_recovered", team=folder.name, method="verified_copy" if recovered else "local_rebuild", deliveries_resent=False)
        return receipt

    def tick(self, original, cancel=None, now=None):
        """One render/recovery per tick. Discovery and previews have their own worker."""
        job = upgrade_job(original)
        if not Path(job["incoming"]).is_dir():
            return {"job": job, "statuses": {t["manifest"]["source_folder"]: {"state": "Retry needed", "message": "Incoming hotfolder is unavailable; waiting for reconnection."} for t in job["teams"]}}
        if Path(job["incoming"]).is_dir():
            job["teams"] = [t for t in job["teams"] if t.get("preview_only") or Path(t["manifest"]["source_folder"]).is_dir()]
            job["selected_team"] = min(job["selected_team"], max(0, len(job["teams"]) - 1))
        validate_job(job, Path.cwd())
        statuses, candidates = {}, []
        for folder, version, stable, message in self.scanner.scan(Path(job["incoming"]), job.get("settle_seconds", 20), job.get("require_ready", False), now):
            statuses[str(folder.resolve())] = {"state": message}
            if stable:
                team = merge_inventory(job, folder)
                team.pop("preview_only", None)
                candidates.append((folder, version, team))
        candidates.sort(key=lambda item: (-len(item[2]["manifest"]["athletes"]), item[0].name))
        sources = {folder.resolve() for folder, *_ in candidates}
        job["teams"] = [t for t in job["teams"] if t.get("preview_only") or not any(
            source != Path(t["manifest"]["source_folder"]).resolve() and source.is_relative_to(Path(t["manifest"]["source_folder"]).resolve()) for source in sources)]
        if job["teams"]:
            job["selected_team"] = min(job["selected_team"], len(job["teams"]) - 1)
        worked, hashed = False, False
        for folder, version, team in candidates:
            source = str(folder.resolve())
            if cancel and cancel.is_set():
                break
            if team.get("edited_completed"):
                statuses[source] = {"state": "Ready to rebuild", "message": "The completed layout was edited. Use Job → Rebuild selected team to export a new version."}
                continue
            # A startup may contain thousands of PNGs. Do not hash every future
            # team's bytes before returning the first visible queue update.
            active = {e["file"] for e in team["manifest"]["athletes"] if not e.get("excluded", False)}
            identity_version = fingerprint(([p for p in version if p[0] in active], team.get("rebuild_nonce", 0)))
            if (worked or hashed) and team.get("content_identity", {}).get("version") != identity_version:
                continue
            try:
                key, newly_hashed = self._identity(job, folder, version, team)
            except OSError:
                hashed = True
                statuses[source] = {"state": "Waiting for copying to finish", "message": "A source changed during the check; inventory will refresh automatically."}
                continue
            hashed |= newly_hashed
            safe_name = re.sub(r"[^\w .-]+", "_", folder.name).strip(" .") or "Team"
            name = f"{safe_name}_team_{key[:16]}.jpg"
            master = self.root / "Masters" / name
            published = Path(job["output"]) / safe_name / name
            previous = self.db.execute("SELECT state,output,receipt,message FROM teams WHERE key=?", (key,)).fetchone()
            if previous and previous[0] == "review":
                reviewed = json.loads(previous[2]) if previous[2] else {}
                if reviewed.get("configuration") != self._configuration(job, team):
                    self._state(key, "interrupted", master, message="Sources or corrections changed; retrying.")
                    previous = None
            if previous and previous[0] == "review":
                statuses[source] = {"state": "Needs attention", "message": previous[3], "key": key}
                continue
            if previous and previous[0] == "completed":
                receipt = json.loads(previous[2])
                protected = Path(receipt.get("master", str(master)))
                try:
                    healthy = protected.is_file() and self._source_hash(protected) == receipt["sha256"]
                except OSError:
                    healthy = False
                if not healthy or not receipt.get("master"):
                    if worked:
                        statuses[source] = {"state": "Recovering finished master", "key": key}
                        continue
                    worked = True
                    try:
                        receipt = self._recover_master(key, previous, job, team, folder, version, master, published, cancel)
                        protected = master
                    except (OSError, ValueError, RuntimeError) as error:
                        statuses[source] = {"state": "Retry needed", "message": "Local master recovery: " + str(error), "key": key}
                        continue
                public = Path(receipt.get("published_output", str(published)))
                statuses[source] = {"state": "Completed", "output": str(protected), "published_output": str(public),
                                    "master": str(protected), "key": key, "message": "",
                                    "notice": "Recovered local image uses current settings. Previous deliveries were not resent." if receipt.get("recovered_from_current_settings") and receipt.get("delivered_sha256") != receipt["sha256"] else ""}
                if receipt.get("base_scale") and job.get("base_scale") is None and not job.get("job_settings", {}).get("transparency_mode"):
                    job["base_scale"] = receipt["base_scale"]
                if receipt.get("capture_canvas") and not job.get("capture_canvas"):
                    job["capture_canvas"] = receipt["capture_canvas"]
                continue
            if key in self.retries and monotonic() < self.retries[key]:
                statuses[source] = {"state": "Retry pending", "message": "Waiting before retrying an unavailable destination.", "key": key}
                continue
            if worked:
                continue
            worked = True
            receipt = None
            try:
                claimed = self._claim(key, master)
                if claimed and claimed[0] == "busy":
                    statuses[source] = {"state": "Processing", "message": claimed[3], "key": key}
                    continue
                self._log("processing", team=folder.name, key=key)
                if claimed and claimed[0] == "routing":
                    receipt = json.loads(claimed[2])
                    existing = Path(receipt.get("master", claimed[1]))
                    if not verify_jpeg(existing, receipt["sha256"]):
                        raise ValueError("The local finished image needs recovery before delivery.")
                    if existing != master:
                        install_copy(existing, master, receipt["sha256"])
                else:
                    receipt = self._render_master(job, team, folder, version, master, cancel)
                receipt.update(master=str(master), published_output=receipt.get("published_output", str(published)))
                self._state(key, "routing", master, receipt)
                self._deliver(key, master, Path(receipt["published_output"]).parent, receipt["sha256"])
                for destination in job.get("routes", []):
                    self._deliver(key, master, destination, receipt["sha256"])
                self._state(key, "completed", master, receipt)
                statuses[source] = {"state": "Completed", "output": str(master), "published_output": receipt["published_output"], "master": str(master), "key": key, "message": ""}
                self._log("completed", team=folder.name, output=str(master), measurement_timings=receipt.get("measurement_timings"), render_timings=receipt.get("render_timings"), source_cache_hits=receipt.get("source_cache_hits", 0))
            except SourceNotSettled as error:
                self._state(key, "interrupted", master, message=str(error))
                statuses[source] = {"state": "Waiting for copying to finish", "message": str(error), "key": key}
            except OSError as error:
                self.retries[key] = monotonic() + 30
                if receipt:
                    self._state(key, "routing", master, receipt, str(error))
                else:
                    self._state(key, "interrupted", master, message=str(error))
                statuses[source] = {"state": "Retry needed", "message": str(error), "key": key}
                self._log("retry", team=folder.name, message=str(error))
            except (ValueError, RuntimeError) as error:
                if receipt:
                    self._state(key, "routing", master, receipt, str(error))
                else:
                    self._state(key, "review", master, receipt={"configuration": self._configuration(job, team)}, message=str(error))
                statuses[source] = {"state": "Needs attention", "message": str(error), "key": key}
                self._log("review", team=folder.name, message=str(error))
        if cancel and cancel.is_set():
            for team in job["teams"]:
                source = str(Path(team["manifest"]["source_folder"]).resolve())
                if statuses.get(source, {}).get("state") == "Queued" and team.get("production"):
                    statuses[source] = deepcopy(team["production"])
        return {"job": job, "statuses": statuses}
