.PHONY: test catalog new clean help

help:
	@echo "make test     - run every problem's tests and doctests"
	@echo "make catalog  - regenerate catalog.md from problem.md front matter"
	@echo "make new      - scaffold: make new CATEGORY=data TITLE='Retry with backoff'"
	@echo "make clean    - remove __pycache__ folders"

test:
	python3 scripts/run_tests.py

catalog:
	python3 scripts/build_catalog.py

new:
	@test -n "$(CATEGORY)" -a -n "$(TITLE)" || (echo "usage: make new CATEGORY=data TITLE='...'" && exit 2)
	python3 scripts/new_problem.py "$(CATEGORY)" "$(TITLE)"

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
