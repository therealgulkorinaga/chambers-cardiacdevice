.PHONY: install dev test lint format typecheck docker-up docker-down run-sim run-dashboard run-api generate-report clean demo generate-templates generate-templates-docker validate-templates download-templates demo-opencarp

install:
	pip install -e .

dev:
	pip install -e ".[all]"

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --tb=short --cov=src --cov-report=html --cov-report=term

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	mypy src/

docker-up:
	docker compose up -d

docker-down:
	docker compose down -v

run-sim:
	python -m src.orchestrator

run-dashboard:
	python -m src.visualization.dashboard.app

run-api:
	uvicorn src.api.main:app --reload --port 8000

demo:
	python scripts/demo.py

demo-30:
	python scripts/demo.py 30 1

demo-365:
	python scripts/demo.py 365 1

demo-population:
	python scripts/demo.py 365 100

generate-report:
	python scripts/generate_report.py

# --- openCARP Template Management ---

generate-templates:
	@echo "Generating openCARP EGM templates (synthetic fallback if openCARP not available)..."
	python -c "from src.generator.cardiac.opencarp.template_generator import TemplateGenerator; TemplateGenerator().generate_all()"
	@echo "Templates generated. Run 'make validate-templates' to verify."

generate-templates-docker:
	@echo "Generating openCARP EGM templates via Docker..."
	docker run --rm -v $$(pwd)/src/generator/cardiac/opencarp/templates:/output opencarp/opencarp:latest \
		python3 -c "from src.generator.cardiac.opencarp.template_generator import TemplateGenerator; TemplateGenerator(output_dir='/output').generate_all()"
	@echo "Templates generated via Docker."

validate-templates:
	python scripts/validate_templates.py

download-templates:
	@echo "Downloading pre-built openCARP templates..."
	@echo "Note: Pre-built templates will be available from GitHub Releases after CI generates them."
	@echo "For now, run 'make generate-templates' to generate locally."

demo-opencarp:
	python -m scripts.demo 30 1 --egm-mode opencarp

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
