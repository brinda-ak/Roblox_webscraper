#!/usr/bin/env python3
"""Compare Roblox item text with tags imported from an existing database."""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
import math
import os
from pathlib import Path
import re
import shlex
import sys
import unicodedata

from tag_database import read_tags

ROOT = Path(__file__).resolve().parent
MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


def clean(value):
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def read_items(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = data.get("records") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("Choose the scraper's snapshot.json (a records list is required).")
    if not rows:
        raise ValueError("This snapshot has no items.")
    return rows


def item_text(row):
    # Put the name first so long descriptions cannot crowd it out of the model input.
    return clean(row.get("name")) + ". " + clean(row.get("description"))


def matches(scores, tags, threshold):
    order = sorted(range(len(tags)), key=lambda i: float(scores[i]), reverse=True)
    def candidate(i):
        result = {"tag": tags[i]["name"], "similarity": round(float(scores[i]), 5)}
        if 'id' in tags[i]:
            result.update(tag_id=tags[i]['id'], kind=tags[i]['kind'])
        return result
    return ([candidate(i) for i in order if float(scores[i]) >= threshold],
            [candidate(i) for i in order[:3]])


def favorites(row):
    value = row.get("favorite_count")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) and value >= 0 else None
    return None


def display_match(match):
    kind = match.get('kind', 'tag')
    return match['tag'] if kind == 'tag' else f"{match['tag']} ({kind})"


def project(embeddings):
    import numpy as np
    if len(embeddings) < 6:
        # UMAP is unstable on tiny inputs. Pad the available PCA axes to 3D.
        centered = embeddings - embeddings.mean(axis=0)
        u, s, _ = np.linalg.svd(centered, full_matrices=False)
        points = np.zeros((len(embeddings), 3))
        width = min(3, len(s))
        points[:, :width] = u[:, :width] * s[:width]
        return points, "PCA (fewer than 6 text-bearing items)"
    import umap
    points = umap.UMAP(n_components=3, n_neighbors=min(15, len(embeddings) - 1),
                       metric="cosine", init="random", random_state=42,
                       n_jobs=1).fit_transform(embeddings)
    return points, "UMAP"


def make_map(rows, coords, metadata, destination):
    import plotly.graph_objects as go
    from plotly.colors import qualitative
    fig = go.Figure()
    groups = {}
    for index, row in enumerate(rows):
        primary = display_match(row["matches"][0]) if row["matches"] else None
        groups.setdefault(primary, []).append(index)
    for group_no, (label, indexes) in enumerate(groups.items()):
        hover, sizes = [], []
        for i in indexes:
            row = rows[i]
            f = favorites(row)
            sizes.append(4 if f is None else 4 + min(13, 1.5 * math.log10(1 + f)))
            candidate_text = "; ".join(f"{display_match(c)} ({c['similarity']:.3f})" for c in row["top_candidates"])
            safe = lambda value: html.escape(str(value), quote=True)
            hover.append(f"<b>{safe(row.get('name') or '(no name)')}</b><br>"
                         f"Favorites: {f:,.0f}<br>" if f is not None else
                         f"<b>{safe(row.get('name') or '(no name)')}</b><br>Favorites: unknown<br>")
            hover[-1] += (f"Suggested tags: {safe(', '.join(display_match(c) for c in row['matches']) or 'Unclassified')}<br>"
                          f"Closest candidates: {safe(candidate_text)}<br>"
                          f"{safe(clean(row.get('description'))[:240])}")
            if row.get('bestselling_rank') is not None:
                hover[-1] += (f"<br>Bestselling position: {safe(row['bestselling_rank'])} "
                              f"in {safe(row.get('category') or 'unknown category')} (all-time sales search)")
        color = "#8995a7" if label is None else qualitative.Safe[group_no % len(qualitative.Safe)]
        fig.add_trace(go.Scatter3d(x=coords[indexes, 0], y=coords[indexes, 1], z=coords[indexes, 2],
                                  mode="markers", name=html.escape(label) if label is not None else "Unclassified",
                                  marker=dict(size=sizes, color=color, opacity=0.8),
                                  text=hover, hovertemplate="%{text}<extra></extra>"))
    axis = dict(showticklabels=False, title="", showbackground=False, zeroline=False,
                gridcolor="#e2e8f0")
    fig.update_layout(template="plotly_white", margin=dict(l=0, r=0, t=10, b=0),
                      scene=dict(xaxis=axis, yaxis=axis, zaxis=axis),
                      legend=dict(title="Top suggested tag"), height=650)
    chart = fig.to_html(full_html=False, include_plotlyjs=True,
                        config={"responsive": True, "displaylogo": False})
    note = html.escape(metadata.get("note", ""))
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Roblox tag explorer</title>
<style>body{{font:16px system-ui,sans-serif;color:#182338;margin:0;background:#f7f9fc}}
main{{max-width:1280px;margin:auto;padding:28px}}h1{{margin:8px 0;font-size:32px}}
p{{line-height:1.6;max-width:1000px}}.small{{color:#536174;font-size:14px}}
.chart{{background:white;border:1px solid #dde3ed;border-radius:14px;overflow:hidden}}</style>
<main><div class="small">MINECRAFT TAGS × ROBLOX ITEMS · EXPERIMENTAL</div>
<h1>Explore items by meaning</h1>
<p>{metadata['item_count']:,} items compared with {metadata['tag_count']:,} imported tags.
Showing {len(rows):,} items in this map. {metadata['matched_item_count']:,} items have a suggested tag
at similarity ≥ {metadata['threshold']:.2f}.</p>
<p class="small">{note}</p><div class="chart">{chart}</div>
<p><b>Drag to rotate · Scroll to zoom · Hover to inspect · Click a legend label to hide it.</b></p>
<p class="small">Position approximates text meaning ({html.escape(metadata['projection'])}).
Larger dots mean more favorites, using a compressed logarithmic scale; unknown favorites use the smallest dot.
Color shows the highest-scoring suggested tag; an item can have several tags.
Favorites do not influence matching or position. Spatial gaps are not proof of real clusters.</p>
<p class="small">Similarity is not a probability or verified label. Scores use the original model vectors,
not distances on this map. The 0.35 default cutoff is an unvalidated starting point for manual review.
All analyzed items and their suggestions are in matches.csv and analysis.json.</p></main></html>"""
    destination.write_text(page, encoding="utf-8")


def csv_safe(value):
    if isinstance(value, str):
        value = re.sub(r'[\x00-\x1f\x7f\u2028\u2029]+', ' ', value)
        if value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
    return value


def analyze(items, tags, output, threshold=0.35, note="", tag_source=None):
    import numpy as np
    from sentence_transformers import SentenceTransformer
    output.mkdir(parents=True, exist_ok=False)
    print(f"Comparing {len(items):,} items with {len(tags):,} imported tags.", flush=True)
    print("Loading AI model (first run downloads it; item text stays on this computer)...", flush=True)
    model = SentenceTransformer(MODEL, revision=MODEL_REVISION, device="cpu", trust_remote_code=False,
                                cache_folder=os.environ.get("ANALYZER_MODEL_CACHE", str(ROOT / ".models")),
                                model_kwargs={"use_safetensors": True})
    texts = [item_text(row) for row in items]
    valid = [i for i, text in enumerate(texts) if text.strip(" .")]
    if not valid:
        raise ValueError("No item names or descriptions are available for analysis.")
    embeddings = model.encode([texts[i] for i in valid], normalize_embeddings=True,
                              batch_size=64, show_progress_bar=True)
    tag_texts = [clean(t["name"] + ". " + t.get("description", "")) for t in tags]
    tag_vectors = model.encode(tag_texts, normalize_embeddings=True, batch_size=64, show_progress_bar=True)
    valid_set = set(valid)
    rows = [{**row, "text_used": texts[i], "matches": [], "top_candidates": [],
             "tag_status": "unclassified" if i in valid_set else "no_text"} for i, row in enumerate(items)]
    # Batch comparisons so a large tag list does not require a full items × tags matrix in memory.
    for start in range(0, len(valid), 256):
        scores = embeddings[start:start + 256] @ tag_vectors.T
        for offset, score in enumerate(scores):
            row = rows[valid[start + offset]]
            row["matches"], row["top_candidates"] = matches(score, tags, threshold)
            row["tag_status"] = "needs_review" if row["matches"] else "unclassified"
    selected = np.sort(np.random.default_rng(42).choice(len(valid), min(5000, len(valid)), replace=False))
    print("Making the 3D map...", flush=True)
    coords, method = project(embeddings[selected])
    map_rows = [rows[valid[i]] for i in selected]
    for row, xyz in zip(map_rows, coords):
        row["map_position"] = [round(float(v), 6) for v in xyz]
    metadata = dict(created_at=datetime.now(timezone.utc).isoformat(), model=MODEL,
                    model_revision=MODEL_REVISION,
                    item_count=len(items), tag_count=len(tags), tag_source=tag_source,
                    matched_item_count=sum(bool(r["matches"]) for r in rows),
                    threshold=threshold, projection=method, map_item_count=len(map_rows),
                    map_limit=5000, random_seed=42, note=note,
                    matching="Cosine similarity of normalized text embeddings; multiple tags or none.",
                    limitations="Unvalidated suggestions, English model, 256 word-piece input truncation. "
                    "Names without definitions can be ambiguous. Map is a sample above 5,000 items.")
    (output / "analysis.json").write_text(json.dumps(dict(metadata=metadata, imported_tags=tags, records=rows),
                                                    ensure_ascii=False, indent=2), encoding="utf-8")
    (output / 'imported_labels.json').write_text(json.dumps(dict(source=tag_source, labels=tags),
                                                ensure_ascii=False, indent=2), encoding='utf-8')
    # Retain the scraper's scalar columns, including bestselling rank, dates, and prices.
    original_fields = list(dict.fromkeys(key for row in items for key, value in row.items()
                           if value is None or isinstance(value, (str, int, float, bool))))
    added = ['suggested_tags', 'suggested_genres', 'suggested_subgenres', 'tag_status',
             'closest_tag', 'closest_similarity', 'closest_tag_id', 'all_matches', 'all_search_rankings']
    fields = [key for key in original_fields if key not in added] + added
    with (output / "matches.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            values = {key: row.get(key) for key in fields}
            top = row["top_candidates"][0] if row["top_candidates"] else {}
            values.update(suggested_tags="; ".join(m["tag"] for m in row["matches"] if m.get('kind','tag')=='tag'),
                          suggested_genres="; ".join(m['tag'] for m in row['matches'] if m.get('kind')=='genre'),
                          suggested_subgenres="; ".join(m['tag'] for m in row['matches'] if m.get('kind')=='subgenre'),
                          closest_tag=top.get("tag"), closest_similarity=top.get("similarity"),
                          closest_tag_id=top.get('tag_id'),
                          all_search_rankings=json.dumps(row.get('rankings',[]),ensure_ascii=False),
                          all_matches=json.dumps(row["matches"], ensure_ascii=False))
            writer.writerow({key: csv_safe(value) for key, value in values.items()})
    with (output / 'matches.csv').open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if sum(1 for _ in reader) != len(rows):
            raise ValueError('CSV row count does not match the analyzed item count.')
    make_map(map_rows, coords, metadata, output / "map.html")
    print(f"Saved {output}\nOpen map.html to explore; matches.csv and analysis.json contain every item.")
    return metadata


def input_path(prompt):
    value = input(prompt).strip()
    if not value:
        raise ValueError("A file is required.")
    # Accept Finder's escaped drag-and-drop paths, and quoted Windows paths.
    if os.name != "nt":
        parts = shlex.split(value)
        if len(parts) == 1:
            value = parts[0]
    return Path(value.strip('"')).expanduser().resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--table")
    parser.add_argument("--tag-column")
    parser.add_argument("--description-column")
    parser.add_argument('--include-genres', action='store_true', help='Also match genre.* and subgenre.* labels from packs.tags')
    parser.add_argument('--note', default='', help='Optional context displayed in the map and analysis metadata')
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
        parser.error("--threshold must be a number between -1 and 1.")
    snapshot = args.snapshot or input_path("Drag your Roblox snapshot.json here, then press Enter: ")
    database = args.database or input_path("Drag your Minecraft tag database here, then press Enter: ")
    items = read_items(snapshot)
    tags, source = read_tags(database, args.table, args.tag_column, args.description_column, args.include_genres)
    if source.get('format') == 'minecraft_packs_json_arrays':
        print(f"Detected Minecraft database: {source['rows_read']:,} listings; using "
              + ('labels, genres and subgenres.' if args.include_genres else 'labels only.'))
    print(f"Imported {len(tags):,} distinct tags. First few: " + ", ".join(t["name"] for t in tags[:12]))
    undefined = sum(not t['description'] for t in tags)
    if undefined:
        print(f'{undefined:,} labels have no definitions. Suggestions based on names alone need review.')
    output = args.output or ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    analyze(items, tags, output, args.threshold, note=args.note, tag_source=source)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Any unfinished output folder can be removed.")
        sys.exit(130)
    except (ValueError, OSError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
