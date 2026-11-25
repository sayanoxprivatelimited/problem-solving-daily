.PHONY: help test check check-docs catalog seo site site-test site-bundle site-shell serve new clean

help:
	@echo "make test      - run every problem's tests + doctests, then the site tests"
	@echo "make check     - everything that must be true before a push (tests, counts, SEO)"
	@echo "make check-docs- verify README's counts against the catalogue"
	@echo "make catalog   - regenerate catalog.md and site/data/problems.json"
	@echo "make seo       - regenerate robots.txt, sitemap.xml and the JSON-LD block"
	@echo "make site-test - run the site's Node tests (layout, wiring, seo)"
	@echo "make serve     - serve the 3D site on http://localhost:8000"
	@echo "make site-bundle - build .site-dist/, the upload-ready static bundle"
	@echo "make site-shell  - build .site-dist-remote/, the one-file CDN shell"
	@echo "make new       - scaffold: make new CATEGORY=data TITLE='Retry with backoff'"
	@echo "make clean     - remove __pycache__ folders"

test:
	python3 scripts/run_tests.py
	@command -v node >/dev/null 2>&1 \
		&& node --test site/tests/*.test.mjs \
		|| echo "node not found - skipping the site tests"

# One command to run before pushing: the suite, the generated files, and every
# number a human could have typed by hand.
check: test
	python3 scripts/build_catalog.py --check
	python3 scripts/build_seo.py --check
	python3 scripts/check_docs.py

check-docs:
	python3 scripts/check_docs.py

catalog:
	python3 scripts/build_catalog.py

seo:
	python3 scripts/build_seo.py

site: catalog seo
	@echo "data ready - now run: make serve"

site-test:
	node --test site/tests/*.test.mjs

site-bundle:
	python3 scripts/build_site_deploy.py --print

site-shell:
	@test -n "$(REF)" || (echo "usage: make site-shell REF=<commit-sha>" && exit 2)
	python3 scripts/build_site_deploy.py --remote-ref $(REF)

serve:
	@echo "serving site/ on http://localhost:8000 (ctrl-c to stop)"
	python3 -m http.server 8000 --directory site

new:
	@test -n "$(CATEGORY)" -a -n "$(TITLE)" || (echo "usage: make new CATEGORY=data TITLE='...'" && exit 2)
	python3 scripts/new_problem.py "$(CATEGORY)" "$(TITLE)"

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
