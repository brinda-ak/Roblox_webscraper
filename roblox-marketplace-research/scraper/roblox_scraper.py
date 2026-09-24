#!/usr/bin/env python3
"""Start a fresh bestselling Roblox collection; explicitly resume when wanted."""
import argparse
from collections import deque
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import csv
import json
import math
from pathlib import Path
import random
import re
import signal
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
API = 'https://catalog.roblox.com/v1/search/items/details'
CATEGORIES = {
    'Clothing': {'Category': 3}, 'Accessories': {'Category': 11},
    'Gear': {'Category': 11, 'Subcategory': 5},
    'Classic T-shirts': {'Category': 3, 'Subcategory': 55},
    'Classic shirts': {'Category': 3, 'Subcategory': 56},
    'Classic pants': {'Category': 3, 'Subcategory': 57},
    'T-shirts': {'Category': 3, 'Subcategory': 58},
    'Shirts': {'Category': 3, 'Subcategory': 59},
    'Pants': {'Category': 3, 'Subcategory': 60},
    'Jackets': {'Category': 3, 'Subcategory': 61},
    'Sweaters': {'Category': 3, 'Subcategory': 62},
    'Shorts': {'Category': 3, 'Subcategory': 63},
    'Shoes': {'Category': 3, 'Subcategory': 64},
    'Dresses and skirts': {'Category': 3, 'Subcategory': 65},
    'Hats': {'Category': 11, 'Subcategory': 54},
    'Hair': {'Category': 18, 'Subcategory': 20},
    'Face accessories': {'Category': 11, 'Subcategory': 21},
    'Neck accessories': {'Category': 11, 'Subcategory': 22},
    'Shoulder accessories': {'Category': 11, 'Subcategory': 23},
    'Front accessories': {'Category': 11, 'Subcategory': 24},
    'Back accessories': {'Category': 11, 'Subcategory': 25},
    'Waist accessories': {'Category': 11, 'Subcategory': 26},
    'All clothing accessories and Gear': {'Category': 1},
}
INCLUDED_TYPES = {2, 8, 11, 12, 19, *range(41, 48), *range(64, 73), 76, 77}
PROFILE = 'bestselling-clothing-accessories-gear-v1'
FIELD_MAP = {
    'id': 'id', 'item_type': 'itemType', 'name': 'name', 'description': 'description',
    'favorite_count': 'favoriteCount', 'purchase_count': 'purchaseCount',
    'price_robux': 'price', 'lowest_price_robux': 'lowestPrice', 'price_status': 'priceStatus',
    'creator_name': 'creatorName', 'creator_id': 'creatorTargetId', 'creator_type': 'creatorType',
    'asset_type': 'assetType', 'bundle_type': 'bundleType', 'created_at': 'itemCreatedUtc',
}
FIELDS = ['category', 'bestselling_rank', 'sort', *FIELD_MAP, 'observed_at', 'url']
RANK_FIELDS = ['item_type', 'id', 'category', 'sort', 'period', 'rank', 'observed_at']


def source_sort(source):
    return 'bestselling'


def source_category(source):
    return source['name']


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def latest_collection():
    paths = sorted(p for p in (ROOT / 'data').glob('collection_*.sqlite') if p.is_file())
    return paths[-1] if paths else None


def network_wait_state(database):
    """A new dataset must still honor a saved server cooldown; never copy item data."""
    if not database or not database.is_file():
        return {}
    db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return {}
        keys = ('cooldown_until', 'next_request_at', 'request_interval', 'rate_limit_streak')
        return {key: json.loads(value) for key, value in db.execute(
            'SELECT key,value FROM settings WHERE key IN (?,?,?,?)', keys)}
    finally:
        db.close()


def choose_database(args):
    if args.database is not None:
        database = args.database.expanduser().resolve()
        if args.export_only and not database.is_file():
            raise ValueError('That database does not exist; there is no saved collection to export.')
        return database, not database.exists(), {}
    last = latest_collection()
    if args.resume or args.export_only:
        if last is None:
            raise ValueError('No collection from this version is saved yet. Start a new collection, or use --database PATH for a specific saved database.')
        return last, False, {}
    # Date and random suffix make each normal launch independent, even within one second.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    database = ROOT / 'data' / f'collection_{stamp}_{uuid.uuid4().hex[:8]}.sqlite'
    waits = network_wait_state(last or ROOT / 'data' / 'bestselling.sqlite')
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch(exist_ok=False)
    return database, True, waits


def parse_amount(text):
    if text.strip().casefold() == 'all':
        return None
    try:
        if int(text) > 0:
            return int(text)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Enter a positive whole number, or all.')


def parse_created(text):
    """Return inclusive UTC calendar dates; this never changes the API sales period."""
    text = text.strip().casefold()
    if text in ('', 'all'):
        return (None, None)
    if text in ('this year', 'this-year'):
        text = str(datetime.now(timezone.utc).year)
    try:
        if re.fullmatch(r'\d{4}', text):
            year = int(text)
            return (date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat())
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}', text):
            start, end = (date.fromisoformat(part) for part in text.split('..'))
            if start <= end:
                return (start.isoformat(), end.isoformat())
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Enter all, this year, a year such as 2026, or YYYY-MM-DD..YYYY-MM-DD (start <= end).')


def creation_day(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc).date().isoformat()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def deleted(name):
    text = unicodedata.normalize('NFKC', str(name or '')).casefold()
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    return bool(re.search(r'content[\W_]*deleted', text))


def csv_value(value):
    if value is None:
        return ''
    text = re.sub(r'[\x00-\x1f\x7f\u2028\u2029]+', ' ', str(value))
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def retry_delay(header, failures):
    # The fallback grows from 60 seconds to 30 minutes; server waits have no cap.
    delay = min(1800, 60 * 2 ** min(failures, 5))
    if header:
        try:
            server = float(header)
        except ValueError:
            try:
                date = parsedate_to_datetime(header)
                server = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                server = 0
        if math.isfinite(server):
            delay = max(delay, server)
    return delay + random.uniform(0, 5)


class Store:
    def __init__(self, database, created=(None, None)):
        database = Path(database)
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database_name = database.name
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.created = created
        # A fresh file keeps the old mixed-sort dataset intact and out of this export.
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            marker = self.db.execute("SELECT value FROM settings WHERE key='profile'").fetchone() if 'settings' in tables else None
            if not marker or marker[0] != json.dumps(PROFILE):
                self.db.close()
                raise ValueError('This is a different/older database. Keep it unchanged and start a new collection without --database.')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS records (item_type TEXT, id INTEGER, payload TEXT, created_day TEXT,
            PRIMARY KEY(item_type,id));
          CREATE INDEX IF NOT EXISTS records_created ON records(created_day);
          CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, query TEXT, cursor TEXT,
            buffer TEXT, offset INTEGER DEFAULT 0, next_cursor TEXT, observed_at TEXT,
            rank INTEGER DEFAULT 0, pages_fetched INTEGER DEFAULT 0, items_saved INTEGER DEFAULT 0,
            items_filtered_out INTEGER DEFAULT 0, items_deleted INTEGER DEFAULT 0, finished INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS cursors (source TEXT, cursor TEXT, PRIMARY KEY(source,cursor));
          CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE IF NOT EXISTS appearances (item_type TEXT, id INTEGER, source TEXT,
            category TEXT, sort TEXT, period TEXT, rank INTEGER, observed_at TEXT,
            PRIMARY KEY(item_type,id,source));
        ''')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('profile', json.dumps(PROFILE)))
            if not tables:
                self.db.execute('INSERT INTO settings VALUES (?,?)', ('collection_created_at', json.dumps(utc_now())))
            for name, query in CATEGORIES.items():
                params = {**query, 'SortType': 2, 'SortAggregation': 5}
                self.db.execute('INSERT OR IGNORE INTO sources(name,query) VALUES (?,?)', (name, json.dumps(params)))

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def settings(self, **values):
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                [(key, json.dumps(value)) for key, value in values.items()])

    def selection(self):
        if self.created == (None, None):
            return '', ()
        return ' WHERE created_day >= ? AND created_day <= ?', self.created

    def selected(self, columns='payload', ordered=True):
        where, params = self.selection()
        return self.db.execute('SELECT '+columns+' FROM records'+where+(' ORDER BY rowid' if ordered else ''), params)

    def count(self):
        return self.selected('count(*)', False).fetchone()[0]

    def matches_created(self, day):
        return self.created == (None, None) or day is not None and self.created[0] <= day <= self.created[1]

    def source(self, name):
        return self.db.execute('SELECT * FROM sources WHERE name=?', (name,)).fetchone()

    def load_page(self, source, page):
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise ValueError('Unexpected API page format; the saved page position was retained.')
        items, next_cursor = page['data'], page.get('nextPageCursor')
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValueError('Invalid pagination cursor; the saved page position was retained.')
        next_cursor = next_cursor or None
        for item in items:
            if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0 or item.get('itemType') not in ('Asset', 'Bundle'):
                raise ValueError('Invalid item ID/type; the saved page position was retained.')
        if next_cursor and (not items or next_cursor == source['cursor'] or self.db.execute(
                'SELECT 1 FROM cursors WHERE source=? AND cursor=?', (source['name'], next_cursor)).fetchone()):
            raise ValueError('Pagination stalled or repeated a cursor. Stopped without declaring completion.')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO cursors VALUES (?,?)', (source['name'], source['cursor'] or ''))
            self.db.execute('''UPDATE sources SET buffer=?,offset=0,next_cursor=?,observed_at=?,
                pages_fetched=pages_fetched+1,finished=? WHERE name=?''',
                (json.dumps(items, ensure_ascii=False) if items else None, next_cursor, utc_now(), int(not items), source['name']))

    def take_one(self, source):
        """Commit an item and its page offset together, so a restart cannot skip it."""
        items = json.loads(source['buffer'])
        offset, rank = source['offset'] + 1, source['rank'] + 1
        item = items[source['offset']]
        is_deleted = deleted(item.get('name'))
        in_scope = (item['itemType'] == 'Asset' and item.get('assetType') in INCLUDED_TYPES
                    or item['itemType'] == 'Bundle' and item.get('bundleType') == 3)
        saved, day = 0, creation_day(item.get('itemCreatedUtc'))
        with self.db:
            if in_scope and not is_deleted:
                row = {key: item.get(value) for key, value in FIELD_MAP.items()}
                for key in ('favorite_count', 'purchase_count'):
                    if type(row[key]) is not int or row[key] < 0:
                        row[key] = None
                path = 'bundles' if item['itemType'] == 'Bundle' else 'catalog'
                row.update(category=source_category(source), bestselling_rank=rank, sort='bestselling', observed_at=source['observed_at'],
                           url=f"https://www.roblox.com/{path}/{item['id']}")
                saved = self.db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?,?)',
                    (item['itemType'], item['id'], json.dumps(row, ensure_ascii=False, allow_nan=False), day)).rowcount
                # Preserve each list's rank even when the item was already saved by another list.
                self.db.execute('INSERT OR IGNORE INTO appearances VALUES (?,?,?,?,?,?,?,?)',
                    (item['itemType'], item['id'], source['name'], source_category(source), source_sort(source),
                     'all-time', rank, source['observed_at']))
            self.db.execute('''UPDATE sources SET offset=?,rank=?,items_saved=items_saved+?,
                items_filtered_out=items_filtered_out+?,items_deleted=items_deleted+? WHERE name=?''',
                (offset, rank, saved, int(not in_scope), int(is_deleted), source['name']))
            if offset == len(items):
                self.db.execute('UPDATE sources SET buffer=NULL,offset=0,cursor=next_cursor,finished=? WHERE name=?',
                                (int(not source['next_cursor']), source['name']))
        return saved if self.matches_created(day) else 0

    def metadata(self, amount, reason, error=None):
        categories = {}
        for source in self.db.execute('SELECT * FROM sources'):
            query = json.loads(source['query'])
            categories[source['name']] = dict(category=source_category(source), sort=source_sort(source), query=query,
                pages_fetched=source['pages_fetched'], items_seen=source['rank'],
                items_saved=source['items_saved'], items_filtered_out=source['items_filtered_out'],
                items_deleted=source['items_deleted'], status='end_of_results' if source['finished'] else 'not_finished')
        limited = [name for name, data in categories.items() if data['status']=='end_of_results' and data['items_seen'] >= 1000]
        total = self.db.execute('SELECT count(*) FROM records').fetchone()[0]
        unknown = self.db.execute('SELECT count(*) FROM records WHERE created_day IS NULL').fetchone()[0]
        return dict(schema_version=4, kind='roblox_bestselling_clothing_accessories_gear_snapshot',
            database_file=self.database_name, collection_created_at=self.setting('collection_created_at'),
            status='complete' if reason in ('requested_amount', 'end_of_results') else 'partial',
            stop_reason=reason, requested_amount=amount if amount is not None else 'all', item_count=self.count(),
            exported_at=utc_now(), source=API, categories=categories, errors=[error] if error else [],
            rate_limit_responses=self.setting('rate_limit_responses', 0), cooldown_until=self.setting('cooldown_until', 0),
            sort='bestselling', sort_period='all-time', page_size=30, full_catalog_guaranteed=False,
            created_from=self.created[0], created_through=self.created[1], date_filter_applied_locally=self.created != (None, None),
            stored_items_all_dates=total, stored_items_missing_creation_date=unknown,
            excluded_by_date_filter=total-self.count(),
            possibly_result_limited_searches=limited,
            notes=['item_count is the unique items matching the chosen creation dates, including earlier runs.',
                   'All encountered in-scope items remain in SQLite so changing the date filter does not lose data.',
                   'Creation dates are filtered locally in UTC; this is not sales during the chosen dates.',
                   'Missing/invalid creation dates are excluded when a date filter is active and counted in metadata.',
                   'Complete means the target or end of these API result lists; it does not mean the entire marketplace.',
                   'A finished search with at least 1,000 entries may have reached the observed search-result limit.',
                   'Bestselling rank is a position within one category search, not a sales total or global rank.',
                   'category/bestselling_rank describe first discovery in an unfiltered all-time sales list. Ranks are not renumbered after filtering.',
                   'Content Deleted names are excluded. Missing counts are unknown, not zero.',
                   'CSV text is flattened to one line; JSON retains the original text.'])

    def export_row(self, payload):
        row = json.loads(payload)
        row['rankings'] = [dict(r) for r in self.db.execute(
            'SELECT category,sort,period,rank,observed_at FROM appearances WHERE item_type=? AND id=? ORDER BY rowid',
            (row['item_type'], row['id']))]
        return row

    def export(self, parent, metadata):
        folder = Path(parent) / datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
        folder.mkdir(parents=True, exist_ok=False)
        with (folder / 'items.csv').open('w', encoding='utf-8-sig', newline='') as c, (folder / 'snapshot.json').open('w', encoding='utf-8') as j:
            writer = csv.DictWriter(c, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            j.write('{"records":[\n')
            count = 0
            for stored in self.selected():
                row = self.export_row(stored[0])
                writer.writerow({key: csv_value(row.get(key)) for key in FIELDS})
                j.write((',\n' if count else '') + json.dumps(row, ensure_ascii=False, allow_nan=False))
                count += 1
            j.write('\n],"metadata":' + json.dumps(metadata, ensure_ascii=False, indent=2) + '}\n')
        # Read every CSV row back and check its values and count against SQLite.
        with (folder / 'items.csv').open(encoding='utf-8-sig', newline='') as stream:
            expected = self.selected()
            verified = 0
            for actual in csv.DictReader(stream, strict=True):
                stored = expected.fetchone()
                row = self.export_row(stored[0]) if stored else None
                if row is None or actual != {key: csv_value(row.get(key)) for key in FIELDS}:
                    raise ValueError('CSV validation failed. Saved records remain in SQLite.')
                verified += 1
            if expected.fetchone() is not None or verified != count or count != metadata['item_count']:
                raise ValueError('Export counts disagree. Saved records remain in SQLite.')
        with (folder / 'rankings.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=RANK_FIELDS, quoting=csv.QUOTE_ALL)
            writer.writeheader()
            for stored in self.selected('item_type,id'):
                for row in self.db.execute('SELECT '+','.join(RANK_FIELDS)+' FROM appearances WHERE item_type=? AND id=? ORDER BY rowid', tuple(stored)):
                    writer.writerow({key: csv_value(row[key]) for key in RANK_FIELDS})
        return folder


class ApiClient:
    def __init__(self, store, stopped=lambda: False):
        self.store, self.stopped = store, stopped

    def wait_until(self, deadline):
        while time.time() < deadline:
            if self.stopped():
                raise KeyboardInterrupt
            time.sleep(min(1, max(0, deadline - time.time())))
        if self.stopped():
            raise KeyboardInterrupt

    def fetch(self, params):
        url = API + '?' + urllib.parse.urlencode(params)
        transient_errors = 0
        while True:
            cooldown = self.store.setting('cooldown_until', 0)
            if cooldown > time.time():
                print(f'Waiting {cooldown - time.time():.0f}s before retrying this same page. Ctrl+C saves and exits.', flush=True)
            self.wait_until(max(cooldown, self.store.setting('next_request_at', 0)))
            interval = self.store.setting('request_interval', 2)
            self.store.settings(next_request_at=time.time() + interval)
            request = urllib.request.Request(url, headers={'User-Agent': 'RobloxBestsellingCollector/4.0', 'Accept': 'application/json'})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    page = json.load(response)
                self.store.settings(cooldown_until=0, rate_limit_streak=0)
                return page
            except urllib.error.HTTPError as exc:
                status, retry_after = exc.code, exc.headers.get('Retry-After') if exc.headers else None
                exc.close()
                if status == 429:
                    streak = self.store.setting('rate_limit_streak', 0)
                    delay = retry_delay(retry_after, streak)
                    self.store.settings(cooldown_until=time.time() + delay, rate_limit_streak=streak + 1,
                        rate_limit_responses=self.store.setting('rate_limit_responses', 0) + 1,
                        request_interval=min(30, interval * 2))
                    print(f'HTTP 429: pausing ALL requests for at least {delay:.0f}s. This page will not be skipped.', flush=True)
                    continue  # No attempt limit for 429; retry the identical URL after the cooldown.
                if status not in (408, 500, 502, 503, 504):
                    raise RuntimeError(f'HTTP {status}. Collection stopped; this page remains pending. See README for resume limits.') from exc
                reason = f'HTTP {status}'
            except (urllib.error.URLError, OSError) as exc:
                retry_after, reason = None, str(exc)
            transient_errors += 1
            delay = retry_delay(retry_after, transient_errors - 1)
            self.store.settings(cooldown_until=time.time() + delay)
            if transient_errors >= 4:
                raise RuntimeError(f'{reason}: repeated non-429 failure. This page remains pending; restart later.')
            print(f'{reason}: waiting {delay:.0f}s, then retrying the same page.', flush=True)


def collect(store, amount, fetch, stopped=lambda: False):
    active = deque(row[0] for row in store.db.execute('SELECT name FROM sources WHERE finished=0 ORDER BY rowid'))
    count, reason, error = store.count(), 'end_of_results', None
    print(f'Collected: {count:,} unique items', flush=True)
    last_update = time.monotonic()
    try:
        while active and (amount is None or count < amount):
            if stopped():
                raise KeyboardInterrupt
            name = active.popleft()
            source = store.source(name)
            if source['buffer'] is None:
                params = {**json.loads(source['query']), 'SortType': 2, 'SortAggregation': 5, 'Limit': 30}
                if source['cursor']:
                    params['Cursor'] = source['cursor']
                store.load_page(source, fetch(params))
                source = store.source(name)
            if source['buffer'] is not None:
                saved = store.take_one(source)
                count += saved
                if saved and count % 100 == 0 or time.monotonic() - last_update >= 30:
                    print(f'Collected: {count:,} unique items', flush=True)
                    last_update = time.monotonic()
            if not store.source(name)['finished']:
                active.append(name)
        if amount is not None and count >= amount:
            reason = 'requested_amount'
    except KeyboardInterrupt:
        reason = 'interrupted'
    except (RuntimeError, ValueError, OSError) as exc:
        reason, error = 'source_error', f'{name}: {exc}'
        print(error, file=sys.stderr, flush=True)
    return store.metadata(amount, reason, error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--amount', type=parse_amount, default=argparse.SUPPRESS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--database', type=Path, help='Explicitly use/create a particular database; an existing file resumes it')
    mode.add_argument('--resume', action='store_true', help='Resume the most recent collection made by this version')
    parser.add_argument('--created', type=parse_created, default=argparse.SUPPRESS,
                        help='all, this year, YYYY, or YYYY-MM-DD..YYYY-MM-DD; inclusive UTC creation dates')
    parser.add_argument('--export-only', action='store_true')
    args = parser.parse_args()
    stopped = [False]
    def stop(signum, frame):
        stopped[0] = True
        print('\nStopping and exporting. A current network request may take up to 30 seconds.', flush=True)
    previous = signal.signal(signal.SIGINT, stop)
    store = None
    try:
        database, fresh, waits = choose_database(args)
        store = Store(database)
        if waits:
            store.settings(**waits)
        print('Roblox bestselling collector - ' + ('new collection.' if fresh else 'using saved collection.'))
        print('Ctrl+C saves and exits.')
        last_created = store.setting('last_created', 'all')
        if hasattr(args, 'created'):
            store.created = args.created
        elif sys.stdin.isatty() and not args.export_only:
            entered = input(f'Created when? all, this year, YYYY, or YYYY-MM-DD..YYYY-MM-DD [{last_created}]: ').strip() or last_created
            store.created = parse_created(entered)
        else:
            store.created = parse_created(last_created)
        created_text = 'all' if store.created == (None, None) else '..'.join(store.created)
        store.settings(last_created=created_text)
        if store.created != (None, None):
            print(f'Created: {created_text} (UTC). Counts include only matching items; bestselling order is all-time.')
        if args.export_only:
            metadata = store.metadata(None, 'export_only')
        else:
            amount = args.amount if hasattr(args, 'amount') else parse_amount(input('Total item target [1000], or all: ').strip() or '1000')
            metadata = collect(store, amount, ApiClient(store, lambda: stopped[0]).fetch, lambda: stopped[0])
        folder = store.export(ROOT / 'exports', metadata)
        print(f"\nVERIFIED EXPORT: {metadata['item_count']:,} rows in BOTH items.csv and snapshot.json.")
        label = {'end_of_results': 'Finished: no more pages returned by Roblox. Full coverage is not guaranteed.',
                 'requested_amount': 'Finished: item target reached.',
                 'interrupted': 'Stopped: progress saved.',
                 'source_error': 'Stopped after an error: progress saved.',
                 'export_only': 'Exported saved collection.'}[metadata['stop_reason']]
        print(f"{label}\n{folder}")
        if metadata['possibly_result_limited_searches']:
            print('Some searches reached at least 1,000 entries and may be limited by Roblox.')
        print('Open items.csv in the export folder above.')
        if metadata['stop_reason'] in ('interrupted', 'source_error'):
            print('Use Resume Last Collection to continue.')
        return 130 if metadata['stop_reason'] == 'interrupted' else 2 if metadata['stop_reason'] == 'source_error' else 0
    except (KeyboardInterrupt, EOFError):
        print('Stopped. Existing SQLite progress is retained; use --export-only to export it.')
        return 130
    except (OSError, ValueError, sqlite3.Error, argparse.ArgumentTypeError) as exc:
        print(f'Could not finish: {exc}. Existing SQLite progress is retained.', file=sys.stderr)
        return 2
    finally:
        if store:
            store.db.close()
        signal.signal(signal.SIGINT, previous)


if __name__ == '__main__':
    raise SystemExit(main())
