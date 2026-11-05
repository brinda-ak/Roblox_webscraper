"""Read Minecraft packs.tags JSON lists or a label table without changing the database."""
from collections import Counter
import json
import sqlite3
from pathlib import Path


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def choose(options, supplied, prompt, optional=False):
    if supplied is not None:
        if optional and supplied == "-":
            return None
        if supplied not in options:
            raise ValueError(f"{supplied!r} is not available. Choices: {', '.join(options)}")
        return supplied
    print("\n" + prompt)
    for i, name in enumerate(options, 1):
        print(f"  {i}. {name}")
    answer = input("Number (Enter to skip): " if optional else "Number: ").strip()
    if optional and not answer:
        return None
    if not answer.isdigit() or not 1 <= int(answer) <= len(options):
        raise ValueError("Please select one of the displayed numbers.")
    return options[int(answer) - 1]


def read_marketplace_labels(connection, path, include_genres=False):
    """Flatten JSON-array or normalized marketplace labels; descriptions are not definitions."""
    counts, row_count = Counter(), 0
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    normalized = False
    if 'pack_tags' in tables:
        columns = {r[1] for r in connection.execute('PRAGMA table_info(pack_tags)')}
        normalized = {'pack_id', 'tag'} <= columns
    if normalized:
        for row_count, (value,) in enumerate(connection.execute('SELECT tag FROM pack_tags'), 1):
            if isinstance(value, str) and value.strip():
                counts[value.strip()] += 1
    else:
        for row_count, (raw,) in enumerate(connection.execute('SELECT tags FROM packs'), 1):
            try:
                values = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid JSON in packs.tags at row {row_count}.') from exc
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f'packs.tags row {row_count} must contain a JSON list of strings.')
            # A repeated label within one listing contributes one occurrence.
            counts.update({value.strip() for value in values if value.strip()})
    allowed = {'tag', 'genre', 'subgenre'} if include_genres else {'tag'}
    labels, kind_counts = [], Counter()
    for identifier_, count in sorted(counts.items()):
        kind, separator, text = identifier_.partition('.')
        recognized = bool(separator and text.strip() and kind in ('tag', 'genre', 'subgenre'))
        kind_counts[kind if recognized else 'other'] += 1
        if not recognized or kind not in allowed:
            continue
        words = text.replace('_', ' ').strip()
        label = dict(id=identifier_, kind=kind, name=words[0].upper()+words[1:],
                     description='', minecraft_pack_count=count)
        if identifier_ == 'tag.rock':
            # Explicit meaning supplied by the user in this project, not inferred from pack text.
            label.update(description='Rock music, rock bands, electric guitars, and rock and roll.',
                         definition_source='User clarified that Rock means rock music.')
        labels.append(label)
    if not labels:
        raise ValueError('No tag.* labels were found in the marketplace label table for the chosen scope.')
    source = dict(file=path.name, table='pack_tags' if normalized else 'packs',
                  tag_column='tag' if normalized else 'tags', description_column=None,
                  format='minecraft_pack_tags_rows' if normalized else 'minecraft_packs_json_arrays', rows_read=row_count,
                  available_label_counts=dict(kind_counts), imported_label_count=len(labels),
                  include_genres=include_genres, skipped_other_values=kind_counts['other'],
                  notes=['Pack/world-type markers are excluded from semantic matching.',
                         'Minecraft product descriptions are not label definitions.',
                         'Raw label IDs are preserved; underscores/prefixes are cleaned for model input.',
                         'Synonyms and singular/plural labels retain their separate source IDs.'])
    return labels, source


def read_tags(path, table=None, tag_column=None, description_column=None, include_genres=False):
    path = Path(path).expanduser().resolve()
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("This version reads SQLite .db/.sqlite files. A .sql text dump needs an "
                             "importer for its database format; share that export so it can be connected.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        tables = [r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not tables:
            raise ValueError("No tables found in this database.")
        if table in (None, 'packs') and tag_column in (None, 'tags') and 'packs' in tables:
            pack_columns = [r[1] for r in connection.execute('PRAGMA table_info(packs)')]
            if 'tags' in pack_columns and 'pack_id' in pack_columns:
                if description_column not in (None, '-'):
                    raise ValueError('packs.description describes products, not label meanings. Skip the description column for this format.')
                return read_marketplace_labels(connection, path, include_genres)
        if table is None and tag_column is None and 'pack_tags' in tables:
            tag_columns = {r[1] for r in connection.execute('PRAGMA table_info(pack_tags)')}
            if {'pack_id', 'tag'} <= tag_columns:
                if description_column not in (None, '-'):
                    raise ValueError('Marketplace pack descriptions are not tag definitions. Skip the description column for this format.')
                return read_marketplace_labels(connection, path, include_genres)
        table = choose(tables, table, "Which table contains the Minecraft tags?")
        columns = [r[1] for r in connection.execute(f"PRAGMA table_info({identifier(table)})")]
        tag_column = choose(columns, tag_column, "Which column contains the tag names?")
        description_column = choose(columns, description_column,
                                    "Optional: which column defines each tag's meaning?", optional=True)
        selected = identifier(tag_column)
        selected += ", " + identifier(description_column) if description_column else ", NULL"
        tags, definitions = {}, {}
        for name, description in connection.execute(f"SELECT {selected} FROM {identifier(table)}"):
            if name is None or not str(name).strip():
                continue
            if not isinstance(name, str):
                raise ValueError("The selected tag column contains non-text values. Choose the tag name column.")
            name = name.strip()
            description = str(description or "").strip()
            key = name.casefold()
            if key not in tags:
                tags[key] = {"name": name, "description": ""}
                definitions[key] = {}
            if description:
                definitions[key][description] = None
        if not tags:
            raise ValueError("No nonempty tags found in that column.")
        for key, tag in tags.items():
            tag["description"] = "; ".join(definitions[key])
        source = dict(file=path.name, table=table, tag_column=tag_column,
                      description_column=description_column)
        return list(tags.values()), source
    except sqlite3.Error as error:
        raise ValueError(f"Could not read SQLite tags: {error}") from error
    finally:
        connection.close()
