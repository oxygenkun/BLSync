
sync:
	uv sync --lock
	
run: sync
	uv run bs -c config/config.toml

format:
	uv run ruff format .

# 本地Docker构建
docker:
	docker build -t blsync:latest .

# 开发环境docker-compose
compose:
	docker compose -f docker-compose-dev.yaml up --build -d

# 构建并推送Docker镜像到Docker Hub（包含版本标签和latest）
docker-release:
	python docker_build_push.py

# 只构建Docker镜像，不推送
docker-build:
	python docker_build_push.py --no-push

# 构建并推送，但跳过Git标签创建
docker-push-only:
	python docker_build_push.py --no-tag

# 指定版本号构建和推送
docker-release-version:
	@if [ -z "$(VERSION)" ]; then echo "请指定版本号: make docker-release-version VERSION=x.x.x"; exit 1; fi
	python docker_build_push.py --version $(VERSION)