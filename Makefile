
sync:
	uv sync
	
run: sync
	uv run bs -c config/config.toml

format:
	uv run ruff format .