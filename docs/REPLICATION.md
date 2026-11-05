# Replication guide

This guide describes the current workflow under `roblox-marketplace-research/`. Commands below are run from a terminal. On Windows, replace `python3` with `py -3` where needed.

## Requirements

- Internet access for Roblox catalog collection and first-time dependency/model downloads.
- Python 3.10 or newer for the collector. The provided analyzer launchers recommend Python 3.12 or 3.13.
- A compatible SQLite label database for analyzer runs. The database is user-provided and is not included in this repository.
- Disk space for collected data, Python packages, the model cache, and result files. The transformer model is downloaded on first analyzer run.

The collector uses only Python's standard library. The analyzer dependencies are pinned in `roblox-marketplace-research/analyzer/requirements.txt`.

## 1. Collect a Roblox snapshot

From the repository root:

```sh
cd roblox-marketplace-research/scraper
python3 roblox_scraper.py --amount 1000
```

The amount is a target for unique items, not a promise that every category will contribute equally. To search until the configured lists are exhausted, use `--amount all`. Without `--amount`, an interactive terminal asks for a target (default 1,000) and a creation-date filter.

The collector rotates through configured categories and records an item's first discovered category/rank plus every category appearance. It stores progress under `scraper/data/` in SQLite. A normal launch starts a new timestamped collection. It does not silently continue an earlier collection.

### Resume, export, and date filters

```sh
# Resume the most recent collector database
python3 roblox_scraper.py --resume --amount 1000

# Resume or create a specific database path
python3 roblox_scraper.py --database ./data/research.sqlite --amount all

# Export the most recent saved collection without making API requests
python3 roblox_scraper.py --export-only

# Collect, but include only items created in calendar year 2025 (UTC)
python3 roblox_scraper.py --amount 1000 --created 2025

# Inclusive UTC date range
python3 roblox_scraper.py --amount 1000 --created 2025-01-01..2025-06-30
```

`--created` filters locally using item creation dates; it does not change Roblox's all-time bestselling query. Accepted values are `all`, `this year`, a four-digit year, or `YYYY-MM-DD..YYYY-MM-DD`. If collection is interrupted or errors, saved SQLite progress remains available. Use `--resume` to continue or `--export-only` to export current saved data. The database profile protects against accidentally reusing older/different schemas.

The export is created in `roblox-marketplace-research/scraper/exports/run_<UTC timestamp>/` and contains:

- `snapshot.json`: records plus collection metadata; this is the analyzer input.
- `items.csv`: flat item fields, suitable for spreadsheets.
- `rankings.csv`: one row per recorded category appearance.

The collector validates the exported CSV against saved SQLite records before reporting a verified export. Keep the SQLite file if you need resume capability; CSV/JSON exports alone do not contain the crawler's pending cursors.

### Launchers

- macOS: double-click `Start Scraper.command` for a new collection or `Resume Last Collection.command` to resume.
- Windows: double-click `Start Scraper.bat` or `Resume Last Collection.bat`.
- To pass flags such as `--created` or `--export-only`, use the command line.

## 2. Prepare a label database

The analyzer accepts a read-only SQLite `.db` or `.sqlite` file. It auto-detects either of these marketplace schemas:

1. A `packs` table with `pack_id` and `tags`, where `tags` contains a JSON array of strings such as `["tag.rock", "genre.pop"]`.
2. A `pack_tags` table with `pack_id` and `tag`, where each row is a pack/tag association such as `tag.rock`.

The importer counts how many packs use each distinct label, excludes non-label markers, and uses `tag.*` labels by default. It also supports ordinary SQLite tables by prompting for a tag column and optional definition column. For custom tables, those fields can instead be provided with `--table`, `--tag-column`, and `--description-column`.

Descriptions should define the meaning of a label. Product or pack descriptions are not label definitions and are not imported by the marketplace-schema path. Add a reliable label definition in the source database or adapt the importer if more contextual definitions are needed. The current project contains one explicit override for `tag.rock` in `analyzer/tag_database.py`.

## 3. Run the analyzer

From the repository root:

```sh
cd roblox-marketplace-research/analyzer
python3 -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python analyze.py \
  --snapshot ../scraper/exports/run_TIMESTAMP/snapshot.json \
  --database /path/to/marketplace.db
```

The output directory defaults to `analyzer/results/<timestamp>/` and must not already exist. On first run, the pinned sentence-transformer model is downloaded and then run locally on CPU. The launcher scripts perform environment setup and prompt for paths when launched without arguments.

Replace `run_TIMESTAMP` in the example command with the actual export folder name printed by the collector.

Useful options:

```sh
python analyze.py --help
python analyze.py --snapshot /path/to/snapshot.json --database /path/to/marketplace.db --include-genres
python analyze.py --snapshot /path/to/snapshot.json --database /path/to/marketplace.db --threshold 0.40 --output ./results/review_run
python analyze.py --snapshot /path/to/snapshot.json --database /path/to/marketplace.db --no-rescue
```

- `--include-genres` includes `genre.*` and `subgenre.*` labels as well as `tag.*` labels.
- `--threshold` controls the minimum cosine similarity for the primary embedding comparison (default `0.35`, range `-1` to `1`). Lower thresholds return more candidates and need more review.
- Name-only suggestions, neighbor voting, and unresolved-item grouping are enabled by default. `--no-rescue` disables all three.
- `--knn-k`, `--knn-min-similarity`, and `--knn-min-vote` adjust neighbor voting. See `python analyze.py --help` for ranges/defaults.
- `--note` adds context to the generated map and metadata.

## 4. Read analyzer outputs

- `matches.csv`: one row per input item, with original scalar fields and suggested labels. `tag_status` is `needs_review`, `unclassified`, or `no_text`; `match_method` identifies `embedding`, `name_only`, or `neighbors`. `closest_tag` and `closest_similarity` come from the original name-plus-description embedding comparison.
- `proposed_groups.csv`: with default rescue settings, a summary of k-means groups among items that remain unmatched after matching. When fewer than 20 items remain, the file is still created with headers and no groups. `--no-rescue` also skips creation of this file.
- `analysis.json`: complete run metadata, imported labels, and analyzed records, including match candidates and rescue settings.
- `imported_labels.json`: the exact label set and source description used for the run.
- `map.html`: interactive 3D projection of up to 5,000 text-bearing items. Open it in a modern browser. Hover details include suggested labels, method, and description.

Treat all automated matches and groups as candidates. The analyzer matches names and descriptions with a sentence-transformer and cosine similarity; it is not trained on a validated Roblox taxonomy. Name-only and neighbor methods are explicitly marked for human review.

## Reproducibility notes

- The model name and immutable model revision are recorded in `analysis.json`.
- UMAP projection and sample selection use fixed random seeds. A sample of up to 5,000 is mapped for large collections.
- Record the snapshot path, database version/hash, command-line options, and run folder with any research findings. Roblox data and source label databases can change between runs.
- Collection completion means the requested target was reached or the queried lists ended; it does not mean the whole marketplace was captured. Inspect `snapshot.json` metadata, especially `status`, `stop_reason`, `possibly_result_limited_searches`, date-filter fields, and `categories`.
- Do not delete the SQLite collection database if you need to resume. Do not commit local marketplace databases, snapshots, exports, model caches, or analysis results unless there is a deliberate data-sharing decision.

## Troubleshooting

- **No Python found:** Install Python and ensure `python3` (macOS/Linux) or `py -3` (Windows) is available on PATH.
- **Analyzer dependency/import error:** Activate the analyzer virtual environment and run `python -m pip install -r requirements.txt`. The launchers also check for scikit-learn before skipping installation.
- **Database not recognized:** Confirm it is a SQLite database (not a `.sql` text dump) and that the intended table/columns match one of the supported schemas. For a custom schema, use the table/column options.
- **HTTP 429 / slow collection:** The collector waits according to Roblox's response and retries the same page. Leave it running, or stop with Ctrl+C and resume later. Avoid running several collectors at once.
- **Partial export:** Check `snapshot.json` metadata and resume the same collection. The export remains useful for analysis, but its coverage status should be reported.
- **Output folder already exists:** Choose a new path with `--output`; analyzer runs deliberately avoid overwriting previous results.
