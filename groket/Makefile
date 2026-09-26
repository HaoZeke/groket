# Local development targets (uv-first), shaped after alisaifee/coredis.

.PHONY: help lint lint-fix lint-complexity test test-cov clean ci install schema schema-check examples-check hud-themes hud-themes-check hud-check hud-cov brand ext native-check

help:
	@echo "groket development targets"
	@echo "  install          project venv (test+dev) + groket on PATH (uv tool)"
	@echo "  lint             ruff check + format --check + mypy + policy scripts"
	@echo "  lint-fix        ruff autofix + format + mypy (whole groket/)"
	@echo "  lint-complexity  size limits only (args/returns/branches/statements/methods); debt report"
	@echo "  schema           regenerate schemas/*.schema.json from Pydantic"
	@echo "  schema-check     fail if committed schemas are out of date"
	@echo "  hud-themes       regenerate groket-hud/assets/textual-themes.json"
	@echo "  hud-themes-check fail if HUD Textual theme map is stale"
	@echo "  hud-check        theme map + rustfmt + clippy + HUD cargo test (+ cov if installed)"
	@echo "  hud-cov          cargo llvm-cov fail-under on non-paint HUD logic (optional)"
	@echo "  examples-check   validate examples/ packs (schema + import contract)"
	@echo "  test             pytest"
	@echo "  test-cov         pytest with coverage report"
	@echo "  brand            rebuild brand/ from the geometric mark"
	@echo "  ext              optional Limited API listwalk (remote builder only)"
	@echo "  native-check     cargo test groket-core (remote builder; not this laptop)"
	@echo "  ci               lint + schema-check + hud-check + examples-check + test"
	@echo "  clean            Python caches plus groket-hud cargo target"

install:
	# Editable project + test/dev groups in .venv (uv run pytest / make test).
	uv sync --group test --group dev
	# Console script on the user PATH (typically ~/.local/bin/groket).
	uv tool install --force --editable .
	@echo "groket installed — try: groket --help   (or: uv run groket)"

lint:
	uv run ruff check --select I groket tests
	uv run ruff check groket tests
	uv run ruff format --check groket tests
	uv run mypy groket
	uv run python scripts/check_fluent.py
	uv run python scripts/check_typing_policy.py

lint-fix:
	uv run ruff check --select I --fix groket tests
	uv run ruff check --fix groket tests
	uv run ruff format groket tests
	uv run mypy groket

# Size-limit rules only (AGENTS §4.6). Not part of make lint / CI.
# PLR0904 (public methods) needs ruff preview. Exit non-zero while debt remains.
lint-complexity:
	uv run ruff check --preview \
		--select PLR0911,PLR0912,PLR0913,PLR0915,PLR0904 \
		groket

# JSON Schema for batch tasks + detection rules (Pydantic sources).
# Committed under schemas/ for editors; published on GitHub Pages at
# https://indynull.github.io/groket/schemas/ (see .github/workflows/pages.yml).
schema:
	uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; emit_tasks_schema(Path('schemas/tasks.schema.json'))"
	uv run python -c "from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; emit_rules_schema(Path('schemas/rules.schema.json'))"

schema-check:
	@tmp=$$(mktemp) && \
	  uv run python -c "from pathlib import Path; from groket.runs.task_schema import emit_tasks_schema; import sys; emit_tasks_schema(Path(sys.argv[1]))" "$$tmp" && \
	  diff -q "$$tmp" schemas/tasks.schema.json >/dev/null || \
	  (echo "schemas/tasks.schema.json is stale — run make schema and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"
	@tmp=$$(mktemp) && \
	  uv run python -c "from pathlib import Path; from groket.engine.rule_schema import emit_rules_schema; import sys; emit_rules_schema(Path(sys.argv[1]))" "$$tmp" && \
	  diff -q "$$tmp" schemas/rules.schema.json >/dev/null || \
	  (echo "schemas/rules.schema.json is stale — run make schema and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"

examples-check:
	uv run python scripts/check_examples.py

hud-themes:
	uv run python scripts/gen_textual_themes.py

hud-themes-check:
	@tmp=$$(mktemp) && \
	  uv run python scripts/gen_textual_themes.py "$$tmp" && \
	  diff -q "$$tmp" groket-hud/assets/textual-themes.json >/dev/null || \
	  (echo "groket-hud/assets/textual-themes.json is stale — run make hud-themes and commit" >&2; rm -f "$$tmp"; exit 1) && \
	  rm -f "$$tmp"

hud-check: hud-themes-check
	cargo fmt --check --manifest-path groket-hud/Cargo.toml
	CARGO_INCREMENTAL=0 cargo clippy --manifest-path groket-hud/Cargo.toml --all-targets -- -D warnings
	CARGO_INCREMENTAL=0 cargo test --manifest-path groket-hud/Cargo.toml
	@$(MAKE) hud-cov

# Paint/window files are omitted from the fail-under (iced view/app loop).
# Paint/window loop and Unix-socket transport are omitted from fail-under.
HUD_COV_OMIT := src/(app|view|typo|main|x11focus|control)\.rs
HUD_COV_FAIL_UNDER := 70

hud-cov:
	@if cargo llvm-cov --version >/dev/null 2>&1; then \
	  CARGO_INCREMENTAL=0 cargo llvm-cov --manifest-path groket-hud/Cargo.toml --lib \
	    --fail-under-lines $(HUD_COV_FAIL_UNDER) \
	    --ignore-filename-regex '$(HUD_COV_OMIT)' \
	    --summary-only && \
	  rm -rf groket-hud/target/llvm-cov-target groket-hud/target/llvm-cov; \
	else \
	  echo "hud-cov: cargo-llvm-cov not installed; skip fail-under (fmt/clippy/test already ran)"; \
	fi

# Logo pack (fonttools/pillow in the brand group; rsvg-convert from librsvg).
brand:
	uv run --group brand python brand/build.py

# Optional CPython Limited API extension (groket._listwalk).
# Must be run on the remote builder. Do not compile on the laptop.
ext:
	GROKET_BUILD_EXT=1 uv sync --reinstall-package groket

# Rust scan leaf. Must be run on the remote builder. Do not compile here.
native-check:
	cargo test --manifest-path native/groket-core/Cargo.toml

test:
	uv run pytest tests/ -q --tb=short

test-cov:
	uv run pytest tests/ --cov=groket --cov-report=term-missing --cov-report=html

ci: lint schema-check hud-check examples-check test

clean:
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .ruff_cache/ .mypy_cache/ \
		htmlcov/ .coverage coverage.json docs/_build/
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	-cargo clean --manifest-path groket-hud/Cargo.toml
