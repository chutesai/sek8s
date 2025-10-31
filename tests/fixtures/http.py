from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def mock_aiohttp_session():
    """Mock aiohttp session"""
    with patch("aiohttp.ClientSession") as mock_client_session:

        def _get_mock_session(mock_response):
            # mock_response = MagicMock()
            # mock_response.status = 200
            # mock_response.json.side_effect = Exception()
            # mock_response.text=AsyncMock(return_value="Success")

            mock_session = MagicMock()
            mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.post.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_session.put.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.put.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_session.delete.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.delete.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_session.patch.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.patch.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_client_session.return_value = mock_session
            mock_client_session.return_value.__aenter__.return_value = mock_session
            mock_client_session.return_value.__aexit__.return_value = None

            return mock_session

        yield _get_mock_session
