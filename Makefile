.PHONY: all bronze silver gold test

all:
	python -m pipeline.run_all

bronze:
	python -m pipeline.run_all --only bronze

silver:
	python -m pipeline.run_all --only silver

gold:
	python -m pipeline.run_all --only gold

test:
	python -m pytest -q

GOLD ?= data/standin_gold

# Everything the Ministry run needs. Reads NO ground truth, so it also works on the hidden test drop.
.PHONY: analytics
analytics:
	python -m analytics.detection.make_alerts --gold $(GOLD)
	python -m analytics.optimise.run_demand --cases $(GOLD)/gold_weekly_cases.csv
	python -m analytics.optimise.run_allocation
	python -m analytics.calendar.preparedness --gold $(GOLD)

# Needs data/truth (ground-truth outbreaks): evaluation for the report, not part of the delivery run.
.PHONY: evaluate
evaluate:
	python -m analytics.detection.run_detection --gold $(GOLD)

# Real gold -> CSV bridge, then the whole analytics run on it.
.PHONY: gold-csv
gold-csv:
	python -m analytics.export_gold --gold data/gold --out data/gold_csv

.PHONY: analytics-real
analytics-real: gold-csv
	$(MAKE) analytics GOLD=data/gold_csv
