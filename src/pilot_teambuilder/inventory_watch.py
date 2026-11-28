"""Metadata-only hotfolder discovery and non-destructive live inventory merging."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .discovery import source_paths, team_folders
from .manifests import inventory


def stamp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(folder, *, strict=True):
    paths = source_paths(folder)
    if any(p.is_symlink() for p in paths):
        raise ValueError('Remove symbolic links from the incoming team folder.')
    if strict and any(p.suffix.lower() in {'.part', '.tmp', '.download', '.crdownload'} for p in paths):
        raise ValueError('Transfer in progress: temporary files remain.')
    return tuple((p.relative_to(folder).as_posix(), s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                 for p in paths if p.is_file() for s in (p.stat(),))


def describe(folder):
    manifest = inventory(folder)
    identities = {}
    for entry in manifest['athletes']:
        stat = (folder / entry['file']).stat()
        if stat.st_ino:
            identities[entry['file']] = [stat.st_dev, stat.st_ino]
    return {'manifest': manifest, 'identities': identities,
            'version': snapshot(folder, strict=False)}


def reconcile_team(team, fresh):
    """Keep surviving/renamed entries; remove all corrections to deleted entries."""
    manifest = team['manifest']
    old = {e['file']: e for e in manifest['athletes']}
    old_ids = team.get('file_identities', {})
    remaining = set(old) - {e['file'] for e in fresh['manifest']['athletes']}
    renamed = {}
    for name in remaining:
        identity = old_ids.get(name)
        if identity is not None:
            renamed.setdefault(tuple(identity), []).append(name)
    entries = []
    for incoming in fresh['manifest']['athletes']:
        name = incoming['file']
        previous = old.get(name)
        identity = fresh['identities'].get(name)
        matches = renamed.get(tuple(identity), []) if identity is not None else []
        if previous is None and len(matches) == 1:
            previous = old[matches.pop()]
        if previous is not None:
            entry = deepcopy(previous)
            entry['file'] = name
        else:
            entry = deepcopy(incoming)
            entry['id'] = 'source-' + stamp(name)[:20]
        entries.append(entry)
    if {e['id'] for e in entries} != {e['id'] for e in manifest['athletes']}:
        manifest.setdefault('corrections', {}).pop('rows', None)
        team.pop('requested_rows', None)
    manifest['athletes'] = entries
    manifest['team_name'] = fresh['manifest']['team_name']
    live = {e['id'] for e in entries}
    corrections = manifest.setdefault('corrections', {})
    if 'offsets_in' in corrections:
        corrections['offsets_in'] = {k: v for k, v in corrections['offsets_in'].items() if k in live}
    for key in ('anchors', 'review_hashes'):
        if key in team:
            team[key] = {k: v for k, v in team[key].items() if k in live}
    new_version = stamp(fresh['version'])
    if team.get('inventory_version') and team['inventory_version'] != new_version:
        team.pop('production', None)
    team['file_identities'] = fresh['identities']
    team['inventory_version'] = new_version
    return team


def merge_inventory(job, folder, fresh=None):
    folder = Path(folder).resolve()
    fresh = fresh or describe(folder)
    team = next((t for t in job['teams'] if Path(t['manifest']['source_folder']).resolve() == folder), None)
    if team is None:
        manifest = deepcopy(fresh['manifest'])
        manifest['settings'] = deepcopy(job.get('job_settings', {}))
        manifest['team_id'] = stamp(str(folder))[:24]
        team = {'manifest': manifest, 'anchors': {}, 'templates': '', 'exports': []}
        job['teams'].append(team)
    reconcile_team(team, fresh)
    return team


class InventoryScanner:
    """One inexpensive scan every ten seconds; never hashes/decodes or exports."""
    def scan(self, root, preview_folders=()):
        root = Path(root).resolve()
        if not root.is_dir():
            raise OSError('Incoming hotfolder is unavailable. Inventory is retained until it reconnects.')
        folders = team_folders(root)
        found, errors = [], {}
        for folder in list(dict.fromkeys([*folders, *(Path(p).resolve() for p in preview_folders)])):
            try:
                if not folder.is_dir():
                    continue  # An external setup preview is allowed to be offline.
                found.append(describe(folder))
            except (OSError, ValueError) as error:
                errors[str(folder)] = str(error)
        return {'incoming': str(root), 'teams': found, 'errors': errors,
                'watched': [str(p.resolve()) for p in folders]}

    def close(self):
        pass


def apply_inventory(job, scan):
    """Apply discovery to the latest UI state so in-flight workers cannot erase edits."""
    result = deepcopy(job)
    if Path(scan['incoming']).resolve() != Path(result['incoming']).resolve():
        raise ValueError('Folder check belongs to another hotfolder.')
    selected = (result['teams'][result['selected_team']]['manifest']['source_folder'] if result['teams'] else None)
    observed = set(scan['watched'])
    errors = scan['errors']
    root = Path(result['incoming']).resolve()
    for fresh in scan['teams']:
        folder = Path(fresh['manifest']['source_folder']).resolve()
        team = merge_inventory(result, folder, fresh)
        if str(folder) in observed:
            team.pop('preview_only', None)
        elif not folder.is_relative_to(root):
            team['preview_only'] = True
    # Remove watched folders that were removed or became empty. Keep unavailable
    # external previews and teams whose directory listing failed.
    result['teams'] = [t for t in result['teams'] if t.get('preview_only')
                       or t['manifest']['source_folder'] in observed or t['manifest']['source_folder'] in errors]
    result['selected_team'] = next((i for i, t in enumerate(result['teams'])
                                    if t['manifest']['source_folder'] == selected), 0)
    return result


def merge_processing_result(current, result, submitted):
    """Only adopt worker inventories when the UI has not edited/refreshed them."""
    updated = deepcopy(current)
    selected = updated['teams'][updated['selected_team']]['manifest']['source_folder'] if updated['teams'] else None
    before = {t['manifest']['source_folder']: t for t in submitted.get('teams', [])}
    local = {t['manifest']['source_folder']: t for t in updated['teams']}
    for remote in result['job']['teams']:
        source = remote['manifest']['source_folder']
        team = local.get(source)
        if team is None:
            if source in before or not Path(source).is_dir():
                continue  # A more recent inventory removed this team.
            team = deepcopy(remote)
            updated['teams'].append(team)
            local[source] = team
        old = before.get(source)
        edited = old is not None and team['manifest'] != old['manifest']
        refreshed = old is not None and team.get('inventory_version') != old.get('inventory_version')
        changed = edited or (refreshed and team.get('inventory_version') != remote.get('inventory_version'))
        if not edited and not refreshed:
            for key in ('manifest', 'file_identities', 'inventory_version', 'content_identity'):
                if key in remote:
                    team[key] = deepcopy(remote[key])
        status = result['statuses'].get(str(Path(source).resolve()), {'state': 'Preview only'} if team.get('preview_only') else {})
        team['production'] = {'state': 'Queued'} if changed and not team.get('preview_only') else deepcopy(status)
    if not updated.get('capture_canvas'):
        updated['capture_canvas'] = result['job'].get('capture_canvas')
    if not updated.get('base_scale') and not updated.get('job_settings', {}).get('transparency_mode'):
        updated['base_scale'] = result['job'].get('base_scale')
    updated['selected_team'] = next((i for i, t in enumerate(updated['teams']) if t['manifest']['source_folder'] == selected), 0)
    return updated
