import asyncio
from itertools import cycle
from typing import Dict
import json
from datasets import load_dataset
from app.core.logger import get_key_manager_logger
from app.core.config import settings

logger = get_key_manager_logger()

class KeyManager:
    def __init__(self, api_keys: list):
        self.api_keys = api_keys
        self.key_cycle = cycle(api_keys)
        self.key_cycle_lock = asyncio.Lock()
        self.failure_count_lock = asyncio.Lock()
        self.key_failure_counts: Dict[str, int] = {key: 0 for key in api_keys}
        self.MAX_FAILURES = settings.MAX_FAILURES
        self.paid_key = settings.PAID_KEY

    @staticmethod
    def fetch_api_keys(dataset_id: str, access_token: str) -> list:
        """从 Hugging Face dataset 中获取 API keys"""
        dataset = load_dataset(dataset_id, use_auth_token=access_token)
        api_keys_json = dataset["train"][:]["api_keys"]  # Assuming there's a column named 'api_keys'
        return json.loads(api_keys_json)

    async def get_paid_key(self) -> str:
        return self.paid_key

    async def get_next_key(self) -> str:
        async with self.key_cycle_lock:
            return next(self.key_cycle)

    async def is_key_valid(self, key: str) -> bool:
        async with self.failure_count_lock:
            return self.key_failure_counts[key] < self.MAX_FAILURES

    async def reset_failure_counts(self):
        async with self.failure_count_lock:
            for key in self.key_failure_counts:
                self.key_failure_counts[key] = 0

    async def get_next_working_key(self) -> str:
        initial_key = await self.get_next_key()
        current_key = initial_key

        while True:
            if await self.is_key_valid(current_key):
                return current_key

            current_key = await self.get_next_key()
            if current_key == initial_key:
                return current_key

    async def handle_api_failure(self, api_key: str) -> str:
        async with self.failure_count_lock:
            self.key_failure_counts[api_key] += 1
            if self.key_failure_counts[api_key] >= self.MAX_FAILURES:
                logger.warning(
                    f"API key {api_key} has failed {self.MAX_FAILURES} times"
                )

        return await self.get_next_working_key()

    def get_fail_count(self, key: str) -> int:
        return self.key_failure_counts.get(key, 0)

    async def get_keys_by_status(self) -> dict:
        valid_keys = {}
        invalid_keys = {}
        
        async with self.failure_count_lock:
            for key in self.api_keys:
                fail_count = self.key_failure_counts[key]
                if fail_count < self.MAX_FAILURES:
                    valid_keys[key] = fail_count
                else:
                    invalid_keys[key] = fail_count
        
        return {
            "valid_keys": valid_keys,
            "invalid_keys": invalid_keys
        }
        
_singleton_instance = None
_singleton_lock = asyncio.Lock()

async def get_key_manager_instance(dataset_id: str, access_token: str) -> KeyManager:
    global _singleton_instance

    async with _singleton_lock:
        if _singleton_instance is None:
            api_keys = KeyManager.fetch_api_keys(dataset_id, access_token)
            if not api_keys:
                raise ValueError("API keys could not be fetched from the dataset")
            _singleton_instance = KeyManager(api_keys)
        return _singleton_instance
