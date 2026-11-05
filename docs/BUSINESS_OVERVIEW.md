# Business overview

## Purpose

Roblox Marketplace Research is a local research workflow for gathering a structured sample of Roblox catalog items and exploring what those items are about. The collector searches Roblox's public catalog API for bestselling clothing, accessories, and gear. The optional analyzer compares item names and descriptions with labels imported from a Minecraft marketplace SQLite database, then produces review files and an interactive map.

The project can help answer questions such as:

- What kinds of items appear in selected bestselling catalog categories?
- Which imported labels appear semantically related to item names and descriptions?
- Which items remain difficult to classify and may merit closer review?
- Where do item groups appear in a map of text similarity?

It is a research aid for exploration and triage. It is not a sales-intelligence system, a complete catalog census, or an authoritative classification service.

## Workflow and deliverables

| Stage | What happens | Main deliverables |
| --- | --- | --- |
| Collect | Queries Roblox catalog search lists in a rotating set of clothing, accessory, and gear categories. Saves progress in SQLite so interrupted work can resume. | `items.csv`, `rankings.csv`, `snapshot.json`, and a persistent SQLite collection database |
| Analyze (optional) | Encodes Roblox item text and imported label text with a pinned sentence-transformer model. Calculates cosine similarity, suggests matches, and groups unresolved items for review. | `matches.csv`, `analysis.json`, `imported_labels.json`, `proposed_groups.csv`, and `map.html` |
| Review | A researcher checks candidate labels, rescue suggestions, group descriptions, and map patterns before drawing conclusions. | Human-reviewed findings outside the automated outputs |

Each collector run writes to its own timestamped export folder. Analyzer results are written to a new timestamped folder by default. Collection databases, exports, model files, and analyzer results are local artifacts and are ignored by Git.

The configured search scope covers clothing, accessories, gear, classic and layered clothing subcategories, hats, hair, and face, neck, shoulder, front, back, and waist accessories. Category searches can overlap; the collector stores each catalog item once and separately records each category appearance.

## How to interpret results

- **Bestselling rank** is an item's position in a particular category search list, using Roblox's all-time bestselling sort. It is not a sales count or a global rank.
- **Coverage is incomplete by design.** The API returns search results, not a guaranteed full catalog. A list with at least 1,000 observed entries may have hit a result limit. Metadata records this possibility.
- **Creation-date filters are local filters.** They filter collected items by item creation date in UTC; they do not change the API's all-time sales ordering and do not mean sales during that period.
- **Similarity is not ground truth.** Embedding similarity ranks text resemblance. Labels without definitions are represented by their names, which can be ambiguous.
- **Rescue suggestions are weaker evidence.** Name-only matches ignore descriptions. Neighbor suggestions inherit the first label from nearby items that received original embedding matches. These are marked for review.
- **Proposed groups are exploratory.** K-means groups unresolved items and lists common words and examples. A group is not a formal category.
- **The map is sampled.** Up to 5,000 text-bearing items are mapped; larger analyses use a deterministic sample. Map positions are a 3D projection of text embeddings, not geographic or business dimensions.
- **Counts and missing values need care.** Missing favorite or purchase counts mean unknown, not zero. API availability and item metadata can change over time.

The analyzer preserves source label IDs and keeps synonyms or singular/plural variants separate. It can import `tag.*` labels and, when enabled, `genre.*` and `subgenre.*` labels. For the current project, `tag.rock` has a manually supplied music meaning; other marketplace pack descriptions are deliberately not treated as label definitions.

## Data, privacy, and operations

The collector sends public catalog search requests to Roblox. It uses a two-second default request interval and honors `Retry-After` or exponential backoff after rate limiting and transient failures. A 429 response pauses collection and retries the same page rather than skipping it. Collection can take a long time; Ctrl+C saves progress and exports the current selection.

The analyzer runs the pinned text model locally on CPU. First-time setup downloads Python packages and model files. The Roblox snapshot and tag database are read locally; analysis results are saved locally. The code does not send item text to an external inference API.

## Stakeholder use

Use this project to generate leads for research, compare catalog language with an existing taxonomy, and focus manual review. Before using results in reporting or decisions:

1. Confirm the collection date, categories, filters, and completion status in `snapshot.json` metadata.
2. Validate a sample of suggested matches against source item text and the intended meaning of each label.
3. Keep original data and automated suggestions distinct from human-approved conclusions.
4. Report search coverage limits and the fact that the ranking is all-time bestselling order.

## Scope and repository structure

- `roblox-marketplace-research/scraper/`: current collector, launchers, persistent databases, and exports.
- `roblox-marketplace-research/analyzer/`: text-matching pipeline, label importer, launchers, dependencies, and analysis results.
- Root `scraper.py` and `database.py`: older, separate keyword-search/storage example; not used by the current end-to-end workflow.
- `docs/`: stakeholder and replication documentation.
