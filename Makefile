.PHONY: install dev test lint demo build clean

install:
	python -m pip install -e ".[mcp]"

dev:
	python -m pip install -e ".[dev,mcp]"

test:
	python -m pytest -q

lint:
	ruff check src tests evals

demo:
	./examples/demo.sh ./demo.clipkit

build:
	python -m pip wheel . --no-deps -w dist

clean:
	rm -rf dist build .pytest_cache .ruff_cache **/__pycache__
