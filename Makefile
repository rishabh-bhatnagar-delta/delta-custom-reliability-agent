.PHONY: help auditor fetcher install clean

help:
	@echo "Available targets:"
	@echo "  auditor     - Run config auditor tool"
	@echo "  fetcher     - Run resource fetcher tool"
	@echo "  install     - Install dependencies"
	@echo "  clean       - Clean temporary files"

auditor:
	python tools/auditor/auditor.py

fetcher:
	python tools/fetcher.py

install:
	pip install -r requirements.txt

clean:
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
