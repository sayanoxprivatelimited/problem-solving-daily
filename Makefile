.PHONY: help test catalog site site-test serve new clean

help:
	@echo "make test      - run every problem's tests + doctests, then the site tests"
	@echo "make catalog   - regenerate catalog.md and site/data/problems.json"
	@echo "make site-test - run the site's Node tests (layout, wiring)"
	@echo "make serve     - serve the 3D site on http://localhost:8000"
	@echo "make new       - scaffold: make new CATEGORY=data TITLE='Retry with backoff'"
	@echo "make clean     - remove __pycache__ folders"

test:
	python3 scripts/run_tests.py
	@command -v node >/dev/null 2>&1 \
		&& node --test site/tests/*.test.mjs \
		|| echo "node not found - skipping the site tests"

catalog:
	python3 scripts/build_catalog.py

site: catalog
	@echo "data ready - now run: make serve"

site-test:
	node --test site/tests/*.test.mjs

serve:
	@echo "serving site/ on http://localhost:8000 (ctrl-c to stop)"
	python3 -m http.server 8000 --directory site

new:
	@test -n "$(CATEGORY)" -a -n "$(TITLE)" || (echo "usage: make new CATEGORY=data TITLE='...'" && exit 2)
	python3 scripts/new_problem.py "$(CATEGORY)" "$(TITLE)"

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
