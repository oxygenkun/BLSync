"""Test FastAPI routes and endpoints."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from blsync.main import app
from blsync.task_models import BiliVideoTaskDAL, TaskStatus


@pytest.fixture
def test_dal():
    """Create test database access layer with in-memory database."""
    dal = BiliVideoTaskDAL("sqlite+aiosqlite:///:memory:")
    asyncio.run(dal.create_tables())
    yield dal
    asyncio.run(dal.close())


@pytest.fixture
def test_client(test_dal):
    """Create test client with mocked database."""
    # Patch get_task_dal to return test database
    with patch("blsync.api.get_task_dal", return_value=test_dal):
        with patch("blsync.main.get_task_dal", return_value=test_dal):
            with patch("blsync.database._task_dal", test_dal):
                yield TestClient(app)


@pytest.fixture
def mock_scraper():
    """Create mock BScraper for video info tests."""
    scraper_instance = MagicMock()
    scraper_instance.get_video_info = AsyncMock()
    with patch("blsync.api.get_global_configs") as mock_config:
        mock_config.return_value = MagicMock()  # Mock config
        with patch("blsync.api.BScraper", return_value=scraper_instance):
            yield scraper_instance


class TestReadRoot:
    """Tests for GET / endpoint."""

    def test_read_root_with_static_file(self, test_dal, tmp_path):
        """Test returning frontend page when static file exists."""
        # Create a temporary static directory with index.html
        static_dir = tmp_path / "static"
        static_dir.mkdir()
        index_file = static_dir / "index.html"
        index_file.write_text("<html><body>Test Page</body></html>")

        with patch("blsync.api.get_task_dal", return_value=test_dal):
            with patch("blsync.api.STATIC_DIR", static_dir):
                client = TestClient(app)
                response = client.get("/")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert b"Test Page" in response.content

    def test_read_root_without_static_file(self, test_dal, tmp_path):
        """Test 404 when static file doesn't exist."""
        # Use a real directory that exists but has no index.html
        empty_dir = tmp_path / "empty_static"
        empty_dir.mkdir()

        with patch("blsync.api.get_task_dal", return_value=test_dal):
            with patch("blsync.api.STATIC_DIR", empty_dir):
                client = TestClient(app)
                response = client.get("/")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCreateTask:
    """Tests for POST /task/bili endpoint."""

    def test_create_task_success(self, test_client):
        """Test successful task creation."""
        task_data = {
            "bid": "BV123456",
            "favid": "fav123",
            "selected_episodes": [0, 1],
        }

        response = test_client.post("/api/task/bili", json=task_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "BV123456" in data["message"]

    def test_create_task_already_queued(self, test_client, test_dal):
        """Test creating a task that already exists in database."""
        task_data = {
            "bid": "BV123456",
            "favid": "fav123",
        }

        # Create task first time
        test_client.post("/api/task/bili", json=task_data)

        # Try to create same task again
        response = test_client.post("/api/task/bili", json=task_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert "updated" in data["message"].lower()

    def test_create_task_already_completed(self, test_client, test_dal):
        """Test creating a task that already exists with COMPLETED status."""
        task_data = {
            "bid": "BV123456",
            "favid": "fav123",
        }

        # Create a completed task
        asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))
        task_key = '{"bvid": "BV123456", "favid": "fav123"}'
        asyncio.run(test_dal.update_task_status(task_key, TaskStatus.COMPLETED))

        # Try to create same task again
        # API first checks has_bili_video_task, which returns True for completed tasks
        response = test_client.post("/api/task/bili", json=task_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert "updated" in data["message"].lower()

    def test_create_task_without_selected_episodes(self, test_client):
        """Test creating task without selected_episodes parameter."""
        task_data = {
            "bid": "BV789",
            "favid": "fav456",
        }

        response = test_client.post("/api/task/bili", json=task_data)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_create_task_with_invalid_data(self, test_client):
        """Test creating task with invalid data."""
        # Missing required field 'bid'
        task_data = {
            "favid": "fav123",
        }

        response = test_client.post("/api/task/bili", json=task_data)

        assert response.status_code == 422  # Validation error


class TestGetTaskStatus:
    """Tests for GET /api/tasks/status endpoint."""

    def test_get_task_status_empty(self, test_client):
        """Test getting task status when no tasks exist."""
        response = test_client.get("/api/tasks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == 0
        assert data["executing"] == 0
        assert data["completed"] == 0
        assert data["failed"] == 0

    def test_get_task_status_with_tasks(self, test_client, test_dal):
        """Test getting task status with multiple tasks."""
        # Create tasks with different statuses
        asyncio.run(test_dal.create_bili_video_task("BV1", "fav1", {}))
        asyncio.run(test_dal.create_bili_video_task("BV2", "fav2", {}))
        asyncio.run(test_dal.create_bili_video_task("BV3", "fav3", {}))

        # Update statuses
        asyncio.run(test_dal.update_task_status('{"bvid": "BV1", "favid": "fav1"}', TaskStatus.COMPLETED))
        asyncio.run(test_dal.update_task_status('{"bvid": "BV2", "favid": "fav2"}', TaskStatus.FAILED, "Test error"))
        asyncio.run(test_dal.update_task_status('{"bvid": "BV3", "favid": "fav3"}', TaskStatus.EXECUTING))

        response = test_client.get("/api/tasks/status")

        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == 0
        assert data["executing"] == 1
        assert data["completed"] == 1
        assert data["failed"] == 1


class TestGetVideoInfo:
    """Tests for GET /api/video/info endpoint."""

    def test_get_video_info_success(self, test_client, mock_scraper):
        """Test getting video info successfully."""
        mock_scraper.get_video_info.return_value = {
            "title": "Test Video",
            "pic": "https://example.com/pic.jpg",
            "desc": "Test description",
            "videos": 2,
            "pages": [
                {"cid": 1, "page": 1, "part": "Part 1"},
                {"cid": 2, "page": 2, "part": "Part 2"},
            ],
            "owner": {
                "name": "Test User",
                "face": "https://example.com/face.jpg",
            },
        }

        response = test_client.get("/api/video/info?bvid=BV123456")

        assert response.status_code == 200
        data = response.json()
        assert data["bvid"] == "BV123456"
        assert data["title"] == "Test Video"
        assert data["pic"] == "https://example.com/pic.jpg"
        assert data["desc"] == "Test description"
        assert data["videos"] == 2
        assert len(data["pages"]) == 2
        assert data["owner"]["name"] == "Test User"

    def test_get_video_info_not_found(self, test_dal):
        """Test getting video info for non-existent video."""
        with patch("blsync.api.get_global_configs") as mock_config:
            mock_config.return_value = MagicMock()
            mock_scraper = MagicMock()
            mock_scraper.get_video_info = AsyncMock(return_value=None)
            with patch("blsync.api.BScraper", return_value=mock_scraper):
                with patch("blsync.api.get_task_dal", return_value=test_dal):
                    client = TestClient(app)
                    response = client.get("/api/video/info?bvid=INVALID")

        assert response.status_code == 404
        assert ("not found" in response.json()["detail"].lower() or
                "not exist" in response.json()["detail"].lower() or
                "失效" in response.json()["detail"])

    def test_get_video_info_missing_bvid(self, test_client):
        """Test getting video info without bvid parameter."""
        response = test_client.get("/api/video/info")

        assert response.status_code == 422  # Validation error


class TestGetTasks:
    """Tests for GET /api/tasks endpoint."""

    def test_get_tasks_empty(self, test_client):
        """Test getting tasks when no tasks exist."""
        response = test_client.get("/api/tasks")

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_get_tasks_with_pagination(self, test_client, test_dal):
        """Test getting tasks with pagination."""
        # Create 25 tasks
        for i in range(25):
            asyncio.run(test_dal.create_bili_video_task(f"BV{i}", "fav1", {}))

        # Get first page (default page_size=20)
        response = test_client.get("/api/tasks?page=1&page_size=10")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 10
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["page_size"] == 10

    def test_get_tasks_with_status_filter(self, test_client, test_dal):
        """Test getting tasks filtered by status."""
        # Create tasks with different statuses
        asyncio.run(test_dal.create_bili_video_task("BV1", "fav1", {}))
        asyncio.run(test_dal.create_bili_video_task("BV2", "fav2", {}))
        asyncio.run(test_dal.create_bili_video_task("BV3", "fav3", {}))

        # Update statuses
        asyncio.run(test_dal.update_task_status('{"bvid": "BV1", "favid": "fav1"}', TaskStatus.COMPLETED))
        asyncio.run(test_dal.update_task_status('{"bvid": "BV2", "favid": "fav2"}', TaskStatus.FAILED, "Test error"))

        # Filter by completed status
        response = test_client.get("/api/tasks?status=completed")

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["status"] == "completed"

    def test_get_tasks_invalid_status(self, test_client):
        """Test getting tasks with invalid status filter."""
        response = test_client.get("/api/tasks?status=invalid_status")

        assert response.status_code == 400
        assert "invalid status" in response.json()["detail"].lower()

    def test_get_tasks_invalid_page_params(self, test_client):
        """Test getting tasks with invalid page parameters."""
        # page must be >= 1
        response = test_client.get("/api/tasks?page=0")

        assert response.status_code == 422

        # page_size must be >= 1 and <= 100
        response = test_client.get("/api/tasks?page_size=0")

        assert response.status_code == 422

        response = test_client.get("/api/tasks?page_size=101")

        assert response.status_code == 422


class TestGetTaskDetail:
    """Tests for GET /api/tasks/{task_id} endpoint."""

    def test_get_task_detail_success(self, test_dal):
        """Test getting task detail successfully."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {"title": "Test"}))

        # Create a proper async context manager mock
        @asynccontextmanager
        async def mock_session_cm():
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none = MagicMock(return_value=task)
            mock_session.execute = AsyncMock(return_value=mock_result)
            yield mock_session

        mock_get_session = MagicMock(return_value=mock_session_cm())

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.get(f"/api/tasks/{task.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task.id
        assert data["status"] == "pending"

    def test_get_task_detail_not_found(self, test_dal):
        """Test getting detail for non-existent task."""
        # Create a proper async context manager mock
        @asynccontextmanager
        async def mock_session_cm():
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none = MagicMock(return_value=None)
            mock_session.execute = AsyncMock(return_value=mock_result)
            yield mock_session

        mock_get_session = MagicMock(return_value=mock_session_cm())

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.get("/api/tasks/99999")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_task_detail_invalid_id(self):
        """Test getting task detail with invalid ID format."""
        client = TestClient(app)
        response = client.get("/api/tasks/invalid")

        assert response.status_code == 422  # Validation error for integer path parameter


class TestUpdateTaskStatus:
    """Tests for PUT /api/tasks/{task_id}/status endpoint."""

    def _create_mock_session(self, task=None):
        """Helper to create a mock session for tests."""
        @asynccontextmanager
        async def mock_session_cm():
            mock_session = MagicMock()

            # Setup execute to return a mock result
            mock_result = MagicMock()
            mock_result.scalar_one_or_none = MagicMock(return_value=task)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_session.commit = AsyncMock()
            mock_session.refresh = AsyncMock()
            mock_session.add = MagicMock()
            yield mock_session

        return mock_session_cm()

    def test_update_status_to_pending(self, test_dal):
        """Test updating task status to pending."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "pending"}
                )

        assert response.status_code == 200

    def test_update_status_to_executing(self, test_dal):
        """Test updating task status to executing."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "executing"}
                )

        assert response.status_code == 200

    def test_update_status_to_completed(self, test_dal):
        """Test updating task status to completed."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "completed"}
                )

        assert response.status_code == 200

    def test_update_status_to_failed_with_error_message(self, test_dal):
        """Test updating task status to failed with error message."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "failed", "error_message": "Download failed"}
                )

        assert response.status_code == 200

    def test_update_status_to_failed_without_error_message(self, test_dal):
        """Test updating task status to failed without error message should fail."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "failed"}
                )

        assert response.status_code == 400
        assert "error_message is required" in response.json()["detail"]

    def test_update_status_invalid_status(self, test_dal):
        """Test updating task status with invalid status value."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={"status": "invalid_status"}
                )

        assert response.status_code == 400
        assert "invalid status" in response.json()["detail"].lower()

    def test_update_status_task_not_found(self, test_dal):
        """Test updating status for non-existent task."""
        mock_get_session = MagicMock(return_value=self._create_mock_session(task=None))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    "/api/tasks/99999/status",
                    json={"status": "completed"}
                )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_status_invalid_task_id(self):
        """Test updating status with invalid task ID format."""
        client = TestClient(app)
        response = client.put(
            "/api/tasks/invalid/status",
            json={"status": "completed"}
        )

        assert response.status_code == 422  # Validation error

    def test_update_status_missing_status_field(self, test_dal):
        """Test updating status without providing status field."""
        # Create a task
        task = asyncio.run(test_dal.create_bili_video_task("BV123456", "fav123", {}))

        mock_get_session = MagicMock(return_value=self._create_mock_session(task))

        with patch.object(test_dal, 'get_session', mock_get_session):
            with patch("blsync.api.get_task_dal", return_value=test_dal):
                client = TestClient(app)
                response = client.put(
                    f"/api/tasks/{task.id}/status",
                    json={}  # Missing status field
                )

        assert response.status_code == 422  # Validation error
