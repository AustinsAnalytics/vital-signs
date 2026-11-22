# Vital Signs: download, build, analyze, render. Each step reads the previous step's output.
PY ?= python

.PHONY: all data analysis site og test serve

all: analysis site test

data:            ## raw Statcast, schedules, transactions and players (slow: about an hour, polite request spacing)
	for s in 2025 2026; do $(PY) pipeline/fetch.py schedule $$s; $(PY) pipeline/fetch.py statcast $$s; $(PY) pipeline/fetch.py players $$s; done
	$(PY) pipeline/fetch.py transactions

analysis:        ## database, filter, models, natural experiment, in-game alert, change detection
	$(PY) pipeline/build.py
	$(PY) pipeline/signals.py
	$(PY) pipeline/warning.py
	$(PY) pipeline/rest.py
	$(PY) pipeline/ingame.py
	$(PY) pipeline/changes.py

site:            ## site data and the page, with every number filled from the outputs
	$(PY) pipeline/export.py
	$(PY) pipeline/story.py

og:              ## the social share card, site/og.png (needs Google Chrome)
	$(PY) tools/og.py

test:
	$(PY) -m pytest -q

serve:
	$(PY) -m http.server 8644 --directory site
