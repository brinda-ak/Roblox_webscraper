# Roblox Marketplace Research

This repository collects public Roblox Marketplace catalog results and provides an optional text-analysis workflow for comparing Roblox item listings with labels from a Minecraft marketplace database.

## Start here

- [Business overview](docs/BUSINESS_OVERVIEW.md): purpose, outputs, interpretation, and limits for stakeholders.
- [Replication guide](docs/REPLICATION.md): setup, collection, analysis, command options, files, and troubleshooting.

The supported end-to-end workflow is in [`roblox-marketplace-research/`](roblox-marketplace-research/). The older `scraper.py` and `database.py` files at the repository root provide a smaller keyword search and SQLite storage example; they are separate from the current collector and analyzer workflow.

## Workflow in one minute

1. Run the collector in `roblox-marketplace-research/scraper/` to build a resumable SQLite collection and export `items.csv`, `rankings.csv`, and `snapshot.json`.
2. Run the analyzer in `roblox-marketplace-research/analyzer/` with a snapshot and a compatible SQLite label database.
3. Review `matches.csv`, `proposed_groups.csv`, `analysis.json`, and `map.html`. Suggestions are analytical aids and require human validation.

Collection and analysis data are local outputs and are excluded from version control. See the replication guide for exact commands and expected files.
