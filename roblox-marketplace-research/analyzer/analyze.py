#!/usr/bin/env python3
"""Compare Roblox item text with tags imported from an existing database."""
import argparse
import csv
from collections import Counter
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

STOPWORDS = {"the", "and", "for", "with", "from", "roblox", "new", "free", "pack", "set",
             "item", "items", "your", "you", "this", "that", "are", "made", "by", "of",
             "in", "on", "to", "thanks", "purchasing", "purchase", "official"}


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


def rescue_by_name(model, rows, valid, tag_vectors, tags, threshold):
    """Retry unmatched items using only their names to avoid generic descriptions."""
    todo = [i for i in valid if not rows[i]["matches"]]
    names = [clean(rows[i].get("name")) for i in todo]
    keep = [k for k, name in enumerate(names) if name]
    if not keep:
        return 0
    vectors = model.encode([names[k] for k in keep], normalize_embeddings=True,
                           batch_size=64, show_progress_bar=False)
    scores = vectors @ tag_vectors.T
    found = 0
    for local, k in enumerate(keep):
        row = rows[todo[k]]
        found_matches, _ = matches(scores[local], tags, threshold)
        if found_matches:
            for match in found_matches:
                match["method"] = "name_only"
            row["matches"] = found_matches
            row["match_method"] = "name_only"
            row["tag_status"] = "needs_review"
            found += 1
    return found


def rescue_by_neighbors(rows, valid, embeddings, k=7, min_similarity=0.45, min_vote=0.6):
    """Assign by weighted votes from the nearest original embedding matches only."""
    import numpy as np

    position = {row_index: p for p, row_index in enumerate(valid)}
    voters = [i for i in valid if rows[i].get("match_method") == "embedding"]
    todo = [i for i in valid if not rows[i]["matches"]]
    if not voters or not todo:
        return 0
    voter_vectors = embeddings[[position[i] for i in voters]]
    voter_labels = [(rows[i]["matches"][0]["tag"],
                     rows[i]["matches"][0].get("kind", "tag")) for i in voters]
    sample_entry = {}
    for i, label in zip(voters, voter_labels):
        sample_entry.setdefault(label, rows[i]["matches"][0])
    k = min(k, len(voters))
    found = 0
    for start in range(0, len(todo), 256):
        chunk = todo[start:start + 256]
        similarities = embeddings[[position[i] for i in chunk]] @ voter_vectors.T
        for local, row_index in enumerate(chunk):
            nearest = np.argsort(similarities[local])[-k:]
            neighbor_scores = similarities[local][nearest]
            votes = {}
            for neighbor, similarity in zip(nearest, neighbor_scores):
                label = voter_labels[neighbor]
                votes[label] = votes.get(label, 0.0) + max(float(similarity), 0.0)
            total = sum(votes.values())
            if total <= 0:
                continue
            winner = max(votes, key=votes.get)
            vote_share = votes[winner] / total
            mean_similarity = float(neighbor_scores.mean())
            if vote_share >= min_vote and mean_similarity >= min_similarity:
                entry = {key: value for key, value in sample_entry[winner].items()
                         if key in ("tag", "tag_id", "kind")}
                entry.update(similarity=round(mean_similarity, 5),
                             vote_share=round(vote_share, 3), method="neighbors")
                rows[row_index]["matches"] = [entry]
                rows[row_index]["match_method"] = "neighbors"
                rows[row_index]["tag_status"] = "needs_review"
                found += 1
    return found


def propose_groups(rows, valid, embeddings, output):
    """Cluster unresolved items for review and write representative words/examples."""
    import numpy as np
    from sklearn.cluster import KMeans

    leftovers = [i for i in valid if not rows[i]["matches"]]
    report = []
    if len(leftovers) >= 20:
        position = {row_index: p for p, row_index in enumerate(valid)}
        vectors = embeddings[[position[i] for i in leftovers]]
        group_count = max(2, min(20, len(leftovers) // 15))
        estimator = KMeans(n_clusters=group_count, n_init=10, random_state=42)
        labels = estimator.fit_predict(vectors)
        for group in range(group_count):
            member_positions = [j for j, label in enumerate(labels) if label == group]
            if not member_positions:
                continue
            members = [leftovers[j] for j in member_positions]
            distances = np.linalg.norm(vectors[member_positions] - estimator.cluster_centers_[group], axis=1)
            examples = [members[j] for j in np.argsort(distances)[:6]]
            words = Counter(word for i in members
                            for word in set(re.findall(r"[a-z][a-z'\\-]{2,}",
                                                       clean(rows[i].get("name")).lower()))
                            if word not in STOPWORDS)
            for i in members:
                rows[i]["proposed_group"] = group + 1
            report.append(dict(group=group + 1, size=len(members),
                               common_words=", ".join(word for word, _ in words.most_common(6)),
                               example_items=" | ".join(clean(rows[i].get("name")) for i in examples)))
    report.sort(key=lambda row: row["size"], reverse=True)
    with (output / "proposed_groups.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["group", "size", "common_words", "example_items"])
        writer.writeheader()
        writer.writerows(report)
    return len(report)


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
                          f"Match method: {safe(row.get('match_method') or 'none')}<br>"
                          f"Proposed review group: {safe(row.get('proposed_group') or 'none')}<br>"
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
    rescue = metadata.get("rescue", {})
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
<p class="small">Extra review-only suggestions: {rescue.get('by_name', 0):,} matched by name alone,
{rescue.get('by_neighbors', 0):,} by neighbors. {rescue.get('unclassified_after', 0):,} items remain unmatched.
Unmatched items may also have proposed groups in proposed_groups.csv.</p>
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


def analyze(items, tags, output, threshold=0.35, note="", tag_source=None,
            rescue=True, knn_k=7, knn_min_similarity=0.45, knn_min_vote=0.6):
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
    for row in rows:
        row["match_method"] = "embedding" if row["matches"] else None
        row["proposed_group"] = None
    unclassified_before = sum(row["tag_status"] == "unclassified" for row in rows)
    rescue_summary = dict(by_name=0, by_neighbors=0, proposed_groups=0,
                          unclassified_before=unclassified_before)
    if rescue:
        print("Rescuing unmatched items (name-only match, then neighbor vote)...", flush=True)
        rescue_summary["by_name"] = rescue_by_name(model, rows, valid, tag_vectors, tags, threshold)
        rescue_summary["by_neighbors"] = rescue_by_neighbors(
            rows, valid, embeddings, k=knn_k, min_similarity=knn_min_similarity,
            min_vote=knn_min_vote)
        rescue_summary["proposed_groups"] = propose_groups(rows, valid, embeddings, output)
    rescue_summary["unclassified_after"] = sum(row["tag_status"] == "unclassified" for row in rows)
    print(f"Unclassified items: {rescue_summary['unclassified_before']:,} -> "
          f"{rescue_summary['unclassified_after']:,} (name-only: +{rescue_summary['by_name']:,}, "
          f"neighbors: +{rescue_summary['by_neighbors']:,}).", flush=True)
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
                    embedding_matched_item_count=sum(r["match_method"] == "embedding" for r in rows),
                    rescue=rescue_summary,
                    rescue_settings=dict(enabled=rescue, knn_k=knn_k,
                                         knn_min_similarity=knn_min_similarity,
                                         knn_min_vote=knn_min_vote),
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
             'closest_tag', 'closest_similarity', 'closest_tag_id', 'all_matches', 'all_search_rankings',
             'match_method', 'proposed_group']
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
                          match_method=row.get('match_method'), proposed_group=row.get('proposed_group'),
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
    parser.add_argument('--no-rescue', action='store_true',
                        help='Skip name-only matching, neighbor voting, and leftover grouping')
    parser.add_argument('--knn-k', type=int, default=7, help='Number of original matches that vote')
    parser.add_argument('--knn-min-similarity', type=float, default=0.45,
                        help='Minimum average cosine similarity to voting neighbors')
    parser.add_argument('--knn-min-vote', type=float, default=0.60,
                        help='Minimum winning share of weighted neighbor votes')
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
        parser.error("--threshold must be a number between -1 and 1.")
    if args.knn_k < 1:
        parser.error('--knn-k must be at least 1.')
    if (not math.isfinite(args.knn_min_similarity) or not -1 <= args.knn_min_similarity <= 1 or
            not math.isfinite(args.knn_min_vote) or not 0 <= args.knn_min_vote <= 1):
        parser.error('--knn-min-similarity must be between -1 and 1; --knn-min-vote between 0 and 1.')
    snapshot = args.snapshot or input_path("Drag your Roblox snapshot.json here, then press Enter: ")
    database = args.database or input_path("Drag your Minecraft tag database here, then press Enter: ")
    items = read_items(snapshot)
    tags, source = read_tags(database, args.table, args.tag_column, args.description_column, args.include_genres)
    source_format = source.get('format')
    if source_format in ('minecraft_packs_json_arrays', 'minecraft_pack_tags_rows'):
        row_kind = 'listings' if source_format == 'minecraft_packs_json_arrays' else 'pack-tag rows'
        print(f"Detected Minecraft database: {source['rows_read']:,} {row_kind}; using "
              + ('labels, genres and subgenres.' if args.include_genres else 'labels only.'))
    print(f"Imported {len(tags):,} distinct tags. First few: " + ", ".join(t["name"] for t in tags[:12]))
    undefined = sum(not t['description'] for t in tags)
    if undefined:
        print(f'{undefined:,} labels have no definitions. Suggestions based on names alone need review.')
    output = args.output or ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    analyze(items, tags, output, args.threshold, note=args.note, tag_source=source,
            rescue=not args.no_rescue, knn_k=args.knn_k,
            knn_min_similarity=args.knn_min_similarity, knn_min_vote=args.knn_min_vote)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Any unfinished output folder can be removed.")
        sys.exit(130)
    except (ValueError, OSError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
