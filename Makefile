.PHONY: bronze
bronze:
	python -m pipeline.bronze.ingest

.PHONY: silver
silver:
	python -m pipeline.silver.build

.PHONY: gold
gold:
	python -m pipeline.gold.build
